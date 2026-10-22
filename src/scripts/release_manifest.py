"""Publish pinned portable updates while keeping the development tree organized."""
from pathlib import Path
import argparse
import hashlib
import json
import os
import re
import subprocess
import tempfile
import io
import platform
import sys
import time
import zipfile

SOURCE = Path(__file__).resolve().parents[1]
ROOT = SOURCE.parent if SOURCE.name == "src" else SOURCE
CHANNELS = {'ds': 'main', 'proxy': 'codex/existing-proxy-models'}
VARIANTS = {(host, channel) for host in ('macos', 'windows') for channel in CHANNELS}
REQUIRED_NATIVE = frozenset((
    'native_catalog_reasoning', 'existing_chat_native_identity_migration',
    'same_thread_model_switching', 'native_auto_manual_compaction_windows',
    'native_lazy_160_mcp_tools', 'native_custom_tool_execution',
    'native_prediction_provider_and_fork', 'gpt_ws_registered_http_auth_isolation',
    'old_encrypted_history_without_gpt_quota', 'native_tools_after_old_history_recovery',
    'native_image_generation', 'native_image_edit', 'native_image_auth_and_saved_files',
    'native_image_websocket_roundtrip',
    'native_websocket_compression',
    'native_adapter_errors_finish_without_idle_wait',
    'native_close_retries_without_idle_wait',
    'native_large_image_history_and_compaction', 'native_http_request_compression',
))
REQUIRED_REGRESSIONS = frozenset((
    'test_native_image_generate_and_edit_keep_official_auth_and_bytes',
    'test_new_native_http_services_are_opaque_and_stay_on_official_origin',
    'test_native_heartbeats_reach_client_during_silent_reasoning',
    'test_relay_forwards_control_frames_once_without_consuming_or_inventing_them',
    'test_deflate_negotiation_large_history_and_server_decline',
    'test_deflate_handles_rfc_vector_fragmentation_context_and_uncompressed_messages',
    'test_deflate_bounds_expanded_data_and_rejects_invalid_frames',
    'test_large_upload_fragments_allow_heartbeat_before_message_finishes',
    'test_official_close_and_external_error_status_reach_native_client_promptly',
    'test_native_streamed_body_still_cancels_when_client_disconnects',
    'test_recheck_keeps_same_release_build_for_close_and_launch',
    'test_launch_failure_releases_claim_and_allows_retry',
    'test_identity_poll_preserves_terminal_and_active_update_progress',
    'test_native_large_image_requests_and_compaction_keep_bytes_and_auth',
    'test_upstream_payload_rejection_is_returned_without_local_retries',
    'test_native_requests_can_exceed_message_limit_without_unbounding_upstream_replies',
    'test_native_compression_preserves_defaults_and_user_choices_across_updates',
    'test_native_policy_is_preserved_and_explicit_compaction_is_not_capped_at_ninety_percent',
    'test_external_slow_reasoning_uses_native_deadline_and_separate_auth',
    'test_native_zstd_roundtrip_invalid_data_and_cancellation',
    'test_provider_owns_reference_count_image_and_response_sizes',
    'test_native_widgets_complete_private_snapshot_and_close_headlessly',
    'test_native_update_card_badge_and_completion_share_the_settings_host',
    'test_native_window_loop_refreshes_and_closes_without_hiding_windows_gui',
    'test_summary_terminal_reasons_are_specific_and_do_not_leak_upstream_text',
    'test_truncated_summary_is_split_then_cached_only_after_full_recovery',
    'test_output_exhaustion_splits_only_failing_input_without_losing_utf8',
    'test_other_provider_failures_are_not_resubmitted_as_smaller_requests',
    'test_output_exhaustion_stops_at_small_input_and_respects_cancellation',
))
PLATFORM_REGRESSIONS = {
    'macos': {'test_native_carbon_queue_reaches_registered_shortcut_handler',
              'test_macos_update_and_rollback_wait_for_old_launchd_job'},
    'windows': {'test_windows_production_host_survives_hide_reopen_and_unreadable_wake',
                'test_windows_routed_buttons_drag_and_tooltips_reach_real_handlers'},
}


def validate_coverage(coverage, edition, host):
    required = REQUIRED_NATIVE | ({'local_flattened_tools', 'native_chat_completions_lazy_tools',
                                  'native_chat_custom_tools_return_gpt'} if edition == 'proxy' else set())
    if (host not in PLATFORM_REGRESSIONS or coverage.get('platform') != host
            or coverage.get('format') != 1 or coverage.get('kind') != 'native-client-with-synthetic-services'
            or any(coverage.get('native', {}).get(name) != 'passed' for name in required)
            or any(coverage.get('regressions', {}).get(name) != 'passed'
                   for name in REQUIRED_REGRESSIONS | PLATFORM_REGRESSIONS[host])):
        raise ValueError('Required release coverage is missing, skipped, or failed')


def git(*args, **kwargs):
    if os.name == 'nt':kwargs.setdefault('creationflags',0x08000000)
    return subprocess.check_output(["git", *args], cwd=ROOT, **kwargs)


def distribution(commit):
    """Build a source-only tree at historical paths for installed old updaters.

    Uses a temporary Git index; never checks out, resets or changes user files.
    The returned commit must be pushed with its release tag before feed use.
    """
    names=git("ls-tree","-r","--name-only",commit,text=True).splitlines()
    if "src/edition.json" not in names:
        return commit
    entries=[];seen=set()
    for name in names:
        if name in ("patch.py","monitor.py","update.json"):
            continue
        target=name.removeprefix("src/")
        if target in seen:
            raise ValueError("Portable release path collision: "+target)
        seen.add(target)
        object_id=git("rev-parse",commit+":"+name,text=True).strip()
        if name in ('README.md','README.en.md'):
            raw=git('show',commit+':'+name).replace(b'src/brand.svg',b'brand.svg')
            object_id=git('hash-object','-w','--stdin',input=raw).decode().strip()
        entries.append("100644 "+object_id+"\t"+target+"\n")
    with tempfile.TemporaryDirectory(prefix="codex-release-index-") as temp:
        env={**os.environ,"GIT_INDEX_FILE":str(Path(temp)/"index")}
        git("read-tree","--empty",env=env)
        git("update-index","--index-info",env=env,input="".join(entries).encode())
        tree=git("write-tree",env=env,text=True).strip()
    return git("commit-tree",tree,"-p",commit,"-m","Package portable source for existing update clients",text=True).strip()


def create(commit,sequence):
    if not re.fullmatch(r"[0-9a-f]{40}",commit) or sequence<1:
        raise ValueError("Use a full committed source SHA and a positive release sequence")
    def read(name):return git("show",commit+":"+name)
    channel=json.loads(read("edition.json"))
    names=git("ls-tree","-r","--name-only",commit,text=True).splitlines()
    files={name:hashlib.sha256(read(name)).hexdigest() for name in names if name!="update.json"}
    if not 8 <= len(files) <= 100:
        raise ValueError('Portable inventory must remain compatible with installed updaters (8-100 files)')
    return {"format":1,**channel,"sequence":sequence,"commit":commit,"files":files,
            "archive": {'format': 'github-zip-v1',
                        'url': 'https://codeload.github.com/limyum-creator/codex-deepseek-patch/zip/' + commit}}


def _json(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + '\n').encode()


def _tree(commit):
    entries = {}
    for row in git('ls-tree', '-r', commit, text=True).splitlines():
        head, name = row.split('\t', 1)
        mode, kind, identity = head.split()
        if mode != '100644' or kind != 'blob':
            raise ValueError('Release sources must be ordinary files: ' + name)
        entries[name] = identity
    return entries


def _commit(entries, parents, message):
    with tempfile.TemporaryDirectory(prefix='codex-paired-index-') as temp:
        env = {**os.environ, 'GIT_INDEX_FILE': str(Path(temp) / 'index')}
        git('read-tree', '--empty', env=env)
        git('update-index', '--index-info', env=env,
            input=''.join('100644 ' + identity + '\t' + name + '\n'
                          for name, identity in sorted(entries.items())).encode())
        tree = git('write-tree', env=env, text=True).strip()
    arguments = ['commit-tree', tree]
    for parent in dict.fromkeys(parents):
        arguments += ['-p', parent]
    return git(*arguments, '-m', message, text=True).strip()


def _blob(raw):
    return git('hash-object', '-w', '--stdin', input=raw).decode().strip()


def core_digest(release):
    return hashlib.sha256(_json({name: digest for name, digest in release['files'].items()
        if name not in ('edition.json', 'README.md', 'README.en.md', 'update.json')})).hexdigest()


def candidate_id(value):
    return hashlib.sha256(_json({k: v for k, v in value.items() if k != 'id'})).hexdigest()


def prepare_pair(commit, output, versions, *, bases=None):
    """Produce both editions from one core, without changing a working tree/ref/feed."""
    commit = git('rev-parse', commit, text=True).strip()
    output = Path(output).resolve()
    if output.exists() and any(output.iterdir()):
        raise ValueError('Candidate output must be empty')
    output.mkdir(parents=True, exist_ok=True)
    entries = _tree(commit)
    if 'src/edition.json' not in entries:
        raise ValueError('Paired candidates require the canonical source tree')
    value = {'format': 1, 'source_commit': commit, 'editions': {}}
    for channel, branch in CHANNELS.items():
        version = versions[channel]
        if not re.fullmatch(r'\d+\.\d+\.\d+(?:-[\w.-]+)?', version):
            raise ValueError('Use a release version for each independent channel')
        base = git('rev-parse', (bases or {}).get(channel, 'origin/' + branch), text=True).strip()
        old = json.loads(git('show', base + ':src/edition.json'))
        previous = json.loads(git('show', base + ':update.json'))
        if old.get('edition') != channel or previous.get('edition') != channel:
            raise ValueError('The channel baseline has a different edition')
        if old['version'] == version:
            raise ValueError('A published version cannot be reused')
        tree = dict(entries)
        metadata = {**old, 'version': version, 'branch': branch, 'edition': channel}
        tree['src/edition.json'] = _blob(_json(metadata))
        tree['update.json'] = _blob(git('show', base + ':update.json'))
        # Both readmes follow the shared candidate, with edition-specific title
        # and entry description supplied by explicit edition blocks.
        for name in ('README.md', 'README.en.md'):
            text = git('show', commit + ':' + name).decode()
            source_version = json.loads(git('show', commit + ':src/edition.json'))['version']
            text = text.replace(source_version, version)
            if channel == 'ds':
                prior = git('show', base + ':' + name).decode()
                # Until a document declares shared edition blocks, preserve the
                # edition's existing introduction and use shared feature docs.
                if '<!-- EDITION:proxy -->' not in text:
                    text = prior.replace(old['version'], version)
                else:
                    text = re.sub(r'<!-- EDITION:proxy -->.*?<!-- /EDITION -->', '', text, flags=re.S)
            else:
                text = re.sub(r'<!-- EDITION:ds -->.*?<!-- /EDITION -->', '', text, flags=re.S)
            text = re.sub(r'<!-- /?EDITION(?::\w+)? -->\n?', '', text)
            tree[name] = _blob(text.encode())
        source = _commit(tree, [base, commit], 'Prepare ' + channel + ' release ' + version + ' from shared core')
        portable = distribution(source)
        release = create(portable, previous['sequence'] + 1)
        release.update(source_commit=source, core_sha256=core_digest(release))
        feed_tree = _tree(source)
        feed_tree['update.json'] = _blob(_json(release))
        feed_commit = _commit(feed_tree, [source], 'Publish verified ' + channel + ' release ' + version)
        package = git('archive', '--format=zip', portable)
        (output / (channel + '.zip')).write_bytes(package)
        value['editions'][channel] = {'release': release, 'feed_commit': feed_commit,
            'expected_head': base, 'archive': channel + '.zip',
            'archive_sha256': hashlib.sha256(package).hexdigest()}
    if len({v['release']['core_sha256'] for v in value['editions'].values()}) != 1:
        raise ValueError('The two editions do not have identical shared code')
    value['id'] = candidate_id(value)
    (output / 'candidate.json').write_bytes(_json(value))
    return value


def read_candidate(path):
    value = json.loads(Path(path).read_bytes())
    if value.get('format') != 1 or value.get('id') != candidate_id(value) or set(value.get('editions', {})) != set(CHANNELS):
        raise ValueError('Invalid candidate identity')
    for channel, item in value['editions'].items():
        release = item['release']
        if release.get('edition') != channel or release.get('branch') != CHANNELS[channel] or core_digest(release) != release['core_sha256']:
            raise ValueError('Candidate channel/core identity changed')
    if len({v['release']['core_sha256'] for v in value['editions'].values()}) != 1:
        raise ValueError('Candidate editions have different cores')
    return value


def verify_candidate(path, channel, app, output):
    """Run actual portable tests/native CLI on this host; emit a content-bound receipt."""
    value = read_candidate(path)
    item = value['editions'][channel]
    host = {'Darwin': 'macos', 'Windows': 'windows'}.get(platform.system())
    if host is None:
        raise ValueError('Native release receipts require macOS or Windows')
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    raw = (Path(path).parent / item['archive']).read_bytes()
    if hashlib.sha256(raw).hexdigest() != item['archive_sha256']:
        raise ValueError('Candidate archive changed')
    with tempfile.TemporaryDirectory(prefix='codex-candidate-') as temp:
        work = Path(temp)
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            if set(archive.namelist()) - {n for n in archive.namelist() if n.endswith('/')} != set(item['release']['files']):
                raise ValueError('Candidate inventory changed')
            for name, digest in item['release']['files'].items():
                if Path(name).is_absolute() or '..' in Path(name).parts or '\\' in name:
                    raise ValueError('Unsafe candidate path')
                data = archive.read(name)
                if hashlib.sha256(data).hexdigest() != digest:
                    raise ValueError('Candidate file changed')
                destination = work / name
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(data)
        env = {**os.environ, 'NO_PROXY': '127.0.0.1,localhost', 'no_proxy': '127.0.0.1,localhost'}
        codec=Path(app)/('Contents/Resources/cua_node/bin/node' if host=='macos' else 'resources/cua_node/bin/node.exe')
        if codec.is_file():env['CODEX_TEST_COMPRESSION_NODE']=str(codec)
        options = {'creationflags': 0x08000000} if os.name == 'nt' else {}
        logs = {}
        for name, arguments in (
            ('unit', ['-m', 'unittest', 'discover', '-s', 'tests', '-v']),
            ('native', ['scripts/test_official_client.py', '--app', str(Path(app).resolve()),
                        '--edition', channel, '--services', '--reportdir', str(work / 'native-report')]),
        ):
            completed = subprocess.run([sys.executable, *arguments], cwd=work, env=env,
                capture_output=True, timeout=600, **options)
            data = completed.stdout + completed.stderr
            (output / (host + '-' + channel + '-' + name + '.log')).write_bytes(data)
            if completed.returncode:
                raise ValueError(name + ' validation failed; see the private validation log')
            logs[name] = hashlib.sha256(data).hexdigest()
        native = json.loads((work / 'native-report' / ('official-client-' + channel + '-result.json')).read_bytes())
        unit_log = (output / (host + '-' + channel + '-unit.log')).read_text(encoding='utf-8', errors='replace')
        passed = set(re.findall(r'^(test_\w+) \([^\n]+\) \.\.\. ok$', unit_log, re.MULTILINE))
        coverage = {'format': 1, 'kind': 'native-client-with-synthetic-services', 'platform': host,
                    'native': native.get('checks', {}),
                    'regressions': {name: 'passed' for name in
                                    (REQUIRED_REGRESSIONS | PLATFORM_REGRESSIONS[host]) & passed}}
        validate_coverage(coverage, channel, host)
        data = _json(coverage)
        (output / (host + '-' + channel + '-coverage.json')).write_bytes(data)
        logs['coverage'] = hashlib.sha256(data).hexdigest()
        receipt = {'format': 1, 'candidate_id': value['id'], 'platform': host, 'edition': channel,
            'commit': item['release']['commit'], 'core_sha256': item['release']['core_sha256'],
            'status': 'passed', 'checks': {'unit': 'passed', 'native': 'passed'}, 'logs': logs}
        if os.name == 'nt':
            import ctypes
            receipt['execution'] = {'console': int(ctypes.windll.kernel32.GetConsoleWindow()),
                                    'admin': bool(ctypes.windll.shell32.IsUserAnAdmin())}
            if receipt['execution'] != {'console': 0, 'admin': False}:
                raise ValueError('Windows release validation must run windowless as the normal user')
        (output / (host + '-' + channel + '.json')).write_bytes(_json(receipt))
    return receipt


def require_receipts(candidate, directory):
    receipts = []
    for host, channel in sorted(VARIANTS):
        path = Path(directory) / (host + '-' + channel + '.json')
        if not path.is_file():
            raise ValueError('Missing validation: ' + host + '/' + channel)
        receipt = json.loads(path.read_bytes())
        release = candidate['editions'][channel]['release']
        expected = {'candidate_id': candidate['id'], 'platform': host, 'edition': channel,
                    'commit': release['commit'], 'core_sha256': release['core_sha256'], 'status': 'passed'}
        if any(receipt.get(k) != v for k, v in expected.items()) or receipt.get('checks') != {'unit': 'passed', 'native': 'passed'}:
            raise ValueError('Validation belongs to another candidate or is incomplete: ' + str(path.name))
        if host == 'windows' and receipt.get('execution') != {'console': 0, 'admin': False}:
            raise ValueError('Windows validation did not use the ordinary hidden execution context')
        for name in ('unit', 'native', 'coverage'):
            log = Path(directory) / (host + '-' + channel + '-' + name + ('.json' if name == 'coverage' else '.log'))
            if not log.is_file() or hashlib.sha256(log.read_bytes()).hexdigest() != receipt.get('logs', {}).get(name):
                raise ValueError('Validation evidence is missing or changed')
            if name == 'coverage':
                validate_coverage(json.loads(log.read_bytes()), channel, host)
        receipts.append(receipt)
    return receipts


def promotion_commands(path, receipts):
    candidate = read_candidate(path)
    require_receipts(candidate, receipts)
    references = []
    for channel, branch in CHANNELS.items():
        item = candidate['editions'][channel]
        if git('rev-parse', 'origin/' + branch, text=True).strip() != item['expected_head']:
            raise ValueError('A published channel advanced; prepare and verify a new candidate')
        references += [item['release']['commit'] + ':refs/tags/v' + item['release']['version'],
                       item['feed_commit'] + ':refs/heads/' + branch]
    return ['git', '-c', 'http.version=HTTP/1.1', 'push', '--atomic', 'origin', *references]


def promote(path, receipts, *, execute=False):
    command = promotion_commands(path, receipts)
    if not execute:
        return {'ready': True, 'command': command, 'published': False}
    candidate = read_candidate(path)
    env = {k: v for k, v in os.environ.items() if k.lower() not in ('http_proxy', 'https_proxy', 'all_proxy')}
    runs = json.loads(subprocess.check_output(['gh', 'api',
        'repos/limyum-creator/codex-deepseek-patch/actions/runs?head_sha=' + candidate['source_commit']],
        env=env, timeout=45))['workflow_runs']
    matching = [v for v in runs if v.get('path') == '.github/workflows/tests.yml' and v.get('head_sha') == candidate['source_commit']]
    if not matching or max(matching, key=lambda v: v['run_number']).get('conclusion') != 'success':
        raise ValueError('The candidate source CI must pass before either channel is published')
    subprocess.run(command, cwd=ROOT, env=env, check=True, timeout=180)
    return {'published': True, 'candidate_id': candidate['id']}


if __name__=="__main__":
    raise SystemExit('Release publishing is not configured for this fork.')
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit")
    parser.add_argument("--sequence",type=int)
    parser.add_argument("--distribution",action="store_true",help="Build a portable source commit from src/")
    parser.add_argument("--output",type=Path,default=ROOT/"update.json")
    parser.add_argument('--prepare-pair', type=Path, metavar='DIRECTORY')
    parser.add_argument('--ds-version')
    parser.add_argument('--proxy-version')
    parser.add_argument('--verify-candidate', type=Path)
    parser.add_argument('--edition', choices=CHANNELS)
    parser.add_argument('--app', type=Path)
    parser.add_argument('--promote', type=Path)
    parser.add_argument('--receipts', type=Path)
    parser.add_argument('--execute', action='store_true', help='Publish only after bound four-variant receipts and CI pass')
    args=parser.parse_args()
    if args.prepare_pair:
        if not args.commit or not args.ds_version or not args.proxy_version:parser.error('Pair preparation needs a commit and both versions')
        print(json.dumps(prepare_pair(args.commit, args.prepare_pair, {'ds': args.ds_version, 'proxy': args.proxy_version}), indent=2))
        raise SystemExit(0)
    if args.verify_candidate:
        if not args.edition or not args.app or args.output == ROOT/'update.json':parser.error('Verification needs edition, app, and a private output directory')
        print(json.dumps(verify_candidate(args.verify_candidate, args.edition, args.app, args.output)))
        raise SystemExit(0)
    if args.promote:
        if not args.receipts:parser.error('Promotion needs four-variant validation receipts')
        print(json.dumps(promote(args.promote, args.receipts, execute=args.execute)))
        raise SystemExit(0)
    if not args.commit or args.sequence is None:parser.error('Use --commit and --sequence, or a candidate operation')
    commit=distribution(args.commit) if args.distribution else args.commit
    value=create(commit,args.sequence)
    if commit!=args.commit:value["source_commit"]=args.commit
    args.output.write_text(json.dumps(value,indent=2)+"\n")
    print(value["edition"],value["version"],"sequence",value["sequence"],"commit",value["commit"])
