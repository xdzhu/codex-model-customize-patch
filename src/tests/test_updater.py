import copy
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch as mock

import monitor
import patch as p
import updater as u


class UpdateTests(unittest.TestCase):
    def test_explicit_checks_and_model_edits_work_with_automatic_checks_disabled(self):
        for force_model,force_code in ((False,False),(True,False),(True,True)):
            with self.subTest(model=force_model,code=force_code),tempfile.TemporaryDirectory() as temp:
                root=Path(temp);obj=object.__new__(u.UpdateBackend)
                obj.directory=root;obj.edition='ds';obj.prepared=False;obj.base=Mock(baseline=None)
                obj.cache={};obj.cache_path=root/'updates.json';obj.package=p.ROOT
                obj.progress=None;obj.warnings=[];obj.model_snapshot=None
                obj.settings={'auto_update':False,'model_sync':False,'force_sync':force_model,'force_code':force_code}
                with mock.object(u,'public_get',side_effect=OSError('offline fixture')) as check, \
                     mock.object(u,'worker',return_value={'edition':'ds','runtime_digest':'fixture','architecture':'official-client-v1'}) as worker, \
                     mock('official_client_update.read_manifest',return_value={}):
                    obj.prepare()
                self.assertEqual(check.call_count,int(force_code))
                self.assertEqual([call.args[1] for call in worker.call_args_list],['identity']+(['sync-models'] if force_model else []))
                self.assertFalse(obj.settings['auto_update']);self.assertFalse(obj.settings['model_sync'])

    def test_explicit_code_check_defers_slow_model_discovery_for_this_check_only(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);obj=object.__new__(u.UpdateBackend)
            obj.directory=root;obj.edition='ds';obj.prepared=False;obj.base=Mock(baseline=None)
            obj.cache={};obj.cache_path=root/'updates.json';obj.package=p.ROOT
            obj.progress=None;obj.warnings=[];obj.model_snapshot=None
            obj.settings={'auto_update':False,'model_sync':True,'force_code':True,'skip_model_sync':True}
            with mock.object(u,'public_get',side_effect=OSError('offline fixture')), \
                 mock.object(u,'worker',return_value={'edition':'ds','runtime_digest':'fixture','architecture':'official-client-v1'}) as worker, \
                 mock('official_client_update.read_manifest',return_value={}):
                obj.prepare()
            self.assertEqual([call.args[1] for call in worker.call_args_list],['identity'])
            self.assertTrue(obj.settings['model_sync'])
            self.assertNotIn('skip_model_sync',obj.settings)

    def test_reopen_status_crosses_real_child_process_boundary(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            (root/'update_worker.py').write_text('raise SystemExit(75)\n')
            with self.assertRaises(p.AppRunningError):u.worker(root,'install',{},root/'job')
            with self.assertRaises(p.PatchError) as error:u.worker(root,'identity',{},root/'job')
            self.assertNotIsInstance(error.exception,p.AppRunningError)

    def test_reopened_install_worker_retains_waiting_progress(self):
        import update_worker
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'job.json';path.write_text('{}');progress=Mock()
            with mock('update_progress.worker_progress',return_value=progress), \
                 mock.object(update_worker,'execute',side_effect=p.AppRunningError('reopened')):
                self.assertEqual(update_worker.main(['install',str(path)]),75)
            progress.publish.assert_called_once_with('waiting_for_exit')
            self.assertFalse(path.with_suffix('.result.json').exists())

    def test_model_sync_is_quiet_only_when_app_and_code_are_unchanged(self):
        app={'version':'v','build':'b','fingerprint':'app','hashes':{'resources/app.asar':'asar'}}
        installed={'architecture':'official-client-v1','status':'installed','app_version':'v','app_build':'b',
                   'original_asar':'asar','update':{'runtime_digest':'runtime','app_fingerprint':'app'}}
        self.assertTrue(u.UpdateBackend.model_only_change(app,'runtime',installed))
        self.assertFalse(u.UpdateBackend.model_only_change(app,'new-runtime',installed))
        self.assertFalse(u.UpdateBackend.model_only_change({**app,'fingerprint':'new-app'},'runtime',installed))
        self.assertFalse(u.UpdateBackend.model_only_change({**app,'version':'new'},'runtime',installed))
        self.assertFalse(u.UpdateBackend.model_only_change(app,'runtime',{**installed,'architecture':'legacy'}))

    def test_quiet_sync_cannot_present_a_panel_or_close_the_desktop(self):
        obj=object.__new__(u.UpdateBackend);obj.quiet_model_sync=True;obj.progress=Mock()
        with mock('update_progress.validated_close') as close:
            for phase in ('available','waiting_for_exit','installing','completed'):obj.progress_phase(phase)
            obj.request_close({'directory':'must-not-read'})
        obj.progress.publish.assert_not_called();close.assert_not_called()

    @unittest.skipUnless(os.name == 'nt', 'Real Windows background worker')
    def test_update_worker_has_no_console_and_keeps_result_file_transport(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root/'update_worker.py').write_text("from pathlib import Path\nimport ctypes,json,sys\np=Path(sys.argv[2]); p.with_suffix('.result.json').write_text(json.dumps({'console':int(ctypes.windll.kernel32.GetConsoleWindow()),'input':json.loads(p.read_text())}))\n", encoding='utf-8')
            self.assertEqual(u.worker(root, 'identity', {'probe':'unchanged'}, root/'job'),
                             {'console':0, 'input':{'probe':'unchanged'}})

    def release(self, edition='ds'):
        names=['patch.py','monitor.py','updater.py','update_worker.py','model_sync.py','edition.json','tests/test_updater.py','tests/test_model_sync.py','tests/route.test.cjs']
        contents={name:b'fixture\n' for name in names}
        contents['edition.json']=json.dumps({'edition':edition,'branch':u.CHANNELS[edition],'version':'1.0.0'}).encode()
        release={'format':1,'edition':edition,'branch':u.CHANNELS[edition],'version':'1.0.0','sequence':2,
                 'commit':'1'*40,'files':{name:hashlib.sha256(raw).hexdigest() for name,raw in contents.items()}}
        return release,contents

    def test_edition_and_branch_are_both_pinned(self):
        release,_=self.release('ds')
        self.assertEqual(u.validate_release(release,'ds'),release)
        with self.assertRaisesRegex(p.PatchError,'different edition'):u.validate_release(release,'proxy')
        release['branch']=u.CHANNELS['proxy']
        with self.assertRaises(p.PatchError):u.validate_release(release,'ds')

    def test_rollback_mutable_commit_and_traversal_are_rejected(self):
        release,_=self.release()
        with self.assertRaisesRegex(p.PatchError,'rollback'):u.validate_release(release,'ds',3)
        changed=copy.deepcopy(release);changed['commit']='main'
        with self.assertRaises(p.PatchError):u.validate_release(changed,'ds')
        for name in ['../secret','/absolute','C:/secret','x/../../secret','x\\secret']:
            changed=copy.deepcopy(release);changed['files'][name]='2'*64
            with self.assertRaises(p.PatchError):u.validate_release(changed,'ds')

    def test_hash_mismatch_never_activates_or_keeps_partial_package(self):
        release,contents=self.release()
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);runtime=root/'runtime';runtime.mkdir();(runtime/'old').write_text('keep')
            with self.assertRaisesRegex(p.PatchError,'digest mismatch'):
                u.source_package(release,root,fetch=lambda _:b'wrong')
            self.assertEqual((runtime/'old').read_text(),'keep')
            self.assertFalse((root/'packages'/release['commit']).exists())

    def test_preflight_isolated_and_cached_files_are_reverified(self):
        release,contents=self.release()
        def fetch(url):return contents[url.split('/'+release['commit']+'/',1)[1]]
        with tempfile.TemporaryDirectory() as tmp, mock.object(u.subprocess,'run',return_value=Mock(returncode=0,stderr='')) as run:
            root=Path(tmp)
            with mock.dict(u.os.environ,{'OPENAI_API_KEY':'must-not-inherit','CODEX_HOME':'private'}):
                target=u.source_package(release,root,fetch)
            self.assertEqual(run.call_count,2)
            for call in run.call_args_list:
                self.assertNotIn('OPENAI_API_KEY',call.kwargs['env'])
                self.assertNotEqual(call.kwargs['env']['CODEX_HOME'],'private')
            self.assertEqual(u.source_package(release,root,lambda _:self.fail('cached package must not redownload')),target)
            (target/'patch.py').write_text('modified')
            with self.assertRaisesRegex(p.PatchError,'digest mismatch'):
                u.source_package(release,root,fetch)

    def test_wrong_package_edition_fails_before_executing_tests(self):
        release,contents=self.release('ds')
        contents['edition.json']=json.dumps({'edition':'proxy','branch':u.CHANNELS['proxy'],'version':'1.0.0'}).encode()
        release['files']['edition.json']=hashlib.sha256(contents['edition.json']).hexdigest()
        with tempfile.TemporaryDirectory() as tmp, mock.object(u.subprocess,'run') as runner:
            with self.assertRaisesRegex(p.PatchError,'edition/version'):
                u.source_package(release,tmp,lambda url:contents[url.split('/'+release['commit']+'/',1)[1]])
            runner.assert_not_called()

    def test_model_or_code_change_triggers_new_composite_identity(self):
        base=u.UpdateBackend.combined('same-app','code-1','models-1')
        self.assertNotEqual(base,u.UpdateBackend.combined('same-app','code-2','models-1'))
        self.assertNotEqual(base,u.UpdateBackend.combined('same-app','code-1','models-2'))
        self.assertEqual(base,u.UpdateBackend.combined('same-app','code-1','models-1'))

    def test_adoption_uses_installed_metadata_not_pending_model_defaults(self):
        obj=object.__new__(u.UpdateBackend);obj.edition='ds';obj.settings={}
        obj.base=Mock();obj.base.adopt.return_value={'fingerprint':'app','baseline':'old'}
        obj.installed_metadata=lambda:{'edition':'ds','runtime_digest':'old-code','model_digest':'old-models'}
        obj.desired_runtime='new-code';obj.model_snapshot={'digest':'new-models'}
        value=obj.adopt()
        self.assertEqual(value['fingerprint'],u.UpdateBackend.combined('app','old-code','old-models'))

    def test_channel_change_cannot_overwrite_existing_monitor(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);current=u.edition()['edition'];other='ds' if current=='proxy' else 'proxy'
            monitor.atomic_json(root/'settings.json',{'edition':other,'app':'untouched'})
            args=Mock(state_dir=root,interval=300,baseline=None)
            with self.assertRaisesRegex(ValueError,'pinned to another edition'), mock.object(monitor.sys,'platform','darwin'):
                monitor.enable(args)
            self.assertEqual(monitor.load_json(root/'settings.json')['app'],'untouched')
            self.assertFalse((root/'runtime').exists())

    def test_preferences_survive_monitor_ticks_and_reset_is_explicit(self):
        with tempfile.TemporaryDirectory() as tmp, mock('builtins.print'):
            root=Path(tmp)
            self.assertEqual(monitor.main(['--state-dir',str(root),'model-options','--model','grok-4.7','--context-window','200000','--hide']),0)
            prefs=monitor.load_json(root/'model-preferences.json')
            self.assertEqual(prefs['context_windows']['grok-4.7'],200000)
            self.assertIn('grok-4.7',prefs['hidden'])
            self.assertEqual(monitor.load_json(root/'updates.json')['models_checked'],0)
            monitor.main(['--state-dir',str(root),'model-options','--model','grok-4.7','--show','--reset-context'])
            prefs=monitor.load_json(root/'model-preferences.json')
            self.assertNotIn('grok-4.7',prefs['hidden'])
            self.assertIsNone(prefs['context_windows']['grok-4.7'])

    def test_runtime_activation_failure_is_recoverable_after_app_commit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);build=root/'build';build.mkdir()
            p.save_json(build/'manifest.json',{'status':'installed'})
            obj=object.__new__(u.UpdateBackend);obj.directory=root;obj.package=root;obj.edition='ds'
            obj.base=Mock();obj.base.snapshot.return_value={'fingerprint':'new-app'}
            obj.snapshot=lambda:{'fingerprint':'candidate'};obj.desired_runtime='code'
            obj.promote=Mock(side_effect=OSError('locked runtime'))
            with mock.object(u,'worker'):
                result=obj.install({'directory':str(build)},{'fingerprint':'candidate','model_digest':'models'})
            self.assertEqual(result['baseline'],str(build))
            self.assertTrue((root/'activation-pending.json').exists())
            obj.prepared=True;obj.promote=Mock()
            # A fresh process retries the journal before model or code discovery.
            obj.prepared=False;obj.settings={'auto_update':False,'model_sync':False};obj.cache={}
            obj.cache_path=root/'updates.json';obj.warnings=[]
            with mock.object(u,'worker',return_value={'edition':'ds','runtime_digest':'code'}):obj.prepare()
            obj.promote.assert_called_once_with(build)
            self.assertFalse((root/'activation-pending.json').exists())

    def test_activation_releases_runtime_working_directory_before_rename(self):
        previous = Path.cwd()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve(); runtime = root / 'runtime'; runtime.mkdir()
            (runtime / 'version').write_text('old')
            package = root / 'package'; package.mkdir(); (package / 'version').write_text('new')
            identity = {'edition': 'proxy', 'files': ['version'], 'runtime_digest': u.runtime_digest(package, ['version'])}
            try:
                u.os.chdir(runtime)
                with mock.object(u, 'worker', return_value=identity):
                    u.activate_runtime(package, root, 'proxy')
                self.assertEqual(Path.cwd(), root)
                self.assertEqual((runtime / 'version').read_text(), 'new')
                self.assertEqual(next(root.glob('runtime-backup-*')).joinpath('version').read_text(), 'old')
            finally:
                u.os.chdir(previous)

    def test_release_cannot_omit_required_regression_tests(self):
        release,_=self.release()
        del release['files']['tests/test_updater.py']
        with self.assertRaisesRegex(p.PatchError,'Incomplete'):u.validate_release(release,'ds')


class ExtensionWorkerTests(unittest.TestCase):
    def test_task_migration_runs_only_after_all_extension_checks_pass(self):
        import update_worker as worker
        for fail in [True, False]:
            with self.subTest(fail=fail), tempfile.TemporaryDirectory() as temp:
                root=Path(temp)
                job={'architecture':'legacy','app':str(root/'app'),'baseline':str(root/'baseline'),'output':str(root/'build'),
                     'settings':{'codex_home':str(root/'home'),'keychain_account':'account',
                                 'keychain_service':'service','state_dir':str(root/'state')}}
                calls=[]
                def verify(output):
                    calls.append('verify')
                    if fail:raise RuntimeError('probe failed')
                with mock.object(worker.backend,'build_compatible',side_effect=lambda _:calls.append('build')), \
                     mock.object(worker,'verify_build',side_effect=verify), \
                     mock.object(monitor,'migrate_windows_working_directory',side_effect=lambda path:calls.append(('migrate',path))):
                    if fail:
                        with self.assertRaisesRegex(RuntimeError,'probe failed'):worker.execute('build',job)
                        self.assertEqual(calls,['build','verify'])
                    else:
                        result=worker.execute('build',job)
                        self.assertEqual(result['status'],'prepared')
                        self.assertEqual(calls,['build','verify',('migrate',str(root/'state'))])

    def test_extension_verification_keeps_large_tool_and_switching_gates(self):
        import update_worker as worker
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);p.save_json(root/'manifest.json',{'model_routes':{'example':{}},
                'switching_probe_required':True,'tool_loading_probe_required':True})
            calls=[]
            from contextlib import ExitStack
            with ExitStack() as stack:
                for name in ['offline','reasoning','context','switching','tools','boot']:
                    stack.enter_context(mock('scripts.test_'+name+'.main',side_effect=lambda _,name=name:calls.append(name)))
                worker.verify_build(root)
            self.assertEqual(calls,['offline','reasoning','context','switching','tools','boot'])


if __name__=='__main__':unittest.main()


class ArchiveAndReleaseTests(unittest.TestCase):
    release = UpdateTests.release
    def archive(self, release, contents, extra=None):
        import io, zipfile
        stream = io.BytesIO()
        prefix = u.REPOSITORY.split('/')[-1] + '-' + release['commit'] + '/'
        with zipfile.ZipFile(stream, 'w') as package:
            for name, raw in {**contents, **(extra or {})}.items():package.writestr(prefix + name, raw)
        return stream.getvalue()

    def test_archive_download_keeps_per_file_integrity_and_old_fallback(self):
        import urllib.error
        release, contents = self.release()
        release['archive'] = u.archive_descriptor(release['commit'])
        archive = self.archive(release, contents)
        with tempfile.TemporaryDirectory() as temp, mock.object(u.subprocess, 'run', return_value=Mock(returncode=0,stderr='')):
            fetch = Mock(return_value=archive)
            root = u.source_package(release, Path(temp), fetch)
            self.assertEqual(fetch.call_count, 1)
            self.assertEqual((root/'edition.json').read_bytes(), contents['edition.json'])
            self.assertTrue((root/'.package.json').is_file())
        calls = []
        def fallback(url):
            calls.append(url)
            if 'codeload.github.com' in url:raise urllib.error.URLError('unavailable')
            return contents[url.split('/'+release['commit']+'/',1)[1]]
        with tempfile.TemporaryDirectory() as temp, mock.object(u.subprocess, 'run', return_value=Mock(returncode=0,stderr='')):
            u.source_package(release, Path(temp), fallback)
        self.assertEqual(len(calls), len(contents)+1)

    def test_archive_corruption_traversal_duplicates_or_foreign_url_never_fall_back(self):
        import io, zipfile
        release, contents = self.release();release['archive'] = u.archive_descriptor(release['commit'])
        corrupt = self.archive(release, {**contents, 'patch.py': b'changed'})
        unsafe = self.archive(release, contents, {'../escape': b'bad'})
        for raw in (corrupt, unsafe):
            with tempfile.TemporaryDirectory() as temp:
                fetch = Mock(return_value=raw)
                with self.assertRaises((p.PatchError, ValueError)):u.source_package(release, Path(temp), fetch)
                self.assertEqual(fetch.call_count,1)
                self.assertFalse((Path(temp)/'escape').exists())
        release['archive']['url'] = 'https://example.com/a.zip'
        with self.assertRaises(p.PatchError):u.validate_release(release,'ds')

    def test_cleanup_preserves_reference_chains_newest_unknown_data_and_other_editions(self):
        import time
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);old=time.time()-20*86400
            monitor.atomic_json(root/'settings.json',{'edition':'ds','baseline':str(root/'builds/active')})
            monitor.atomic_json(root/'state.json',{'phase':'installed'})
            for name in ('ancestor','active','orphan','fresh','other'):
                path=root/'builds'/name;path.mkdir(parents=True)
                manifest={'edition':'proxy' if name=='other' else 'ds','architecture':'official-client-v1','status':'prepared'}
                if name=='active':manifest['legacy_baseline']=str(root/'builds/ancestor')
                monitor.atomic_json(path/'manifest.json',manifest)
                os.utime(path,(old,old))
            (root/'builds/unknown').mkdir();(root/'builds/unknown/personal.txt').write_text('preserve')
            os.utime(root/'builds/active',(time.time(),time.time()))
            os.utime(root/'builds/fresh',(time.time(),time.time()))
            preview=u.prune_update_cache(root)
            self.assertEqual([v['name'] for v in preview['items']],['orphan'])
            self.assertTrue((root/'builds/orphan').exists())
            result=u.prune_update_cache(root,dry_run=False)
            self.assertEqual(result['bytes'],preview['bytes'])
            self.assertFalse((root/'builds/orphan').exists())
            for name in ('ancestor','active','fresh','other','unknown'):self.assertTrue((root/'builds'/name).exists())

    def test_cleanup_prunes_unreferenced_finished_transactions_but_keeps_incomplete_recovery(self):
        import time
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);old=time.time()-20*86400
            monitor.atomic_json(root/'settings.json',{'edition':'ds','baseline':str(root/'builds/active')})
            monitor.atomic_json(root/'state.json',{'phase':'installed'})
            for name,phase in [('active','committed'),('recent','committed'),('orphan','committed'),
                               ('uninstalled','complete'),('pending','config_installed')]:
                path=root/'builds'/name;path.mkdir(parents=True)
                monitor.atomic_json(path/'manifest.json',{'edition':'ds','architecture':'official-client-v1','status':'installed'})
                monitor.atomic_json(path/('native-uninstall-transaction.json' if name=='uninstalled' else 'native-install-transaction.json'),{'phase':phase})
                age=time.time() if name in ('active','recent') else old
                os.utime(path,(age,age))
            self.assertEqual({v['name'] for v in u.prune_update_cache(root)['items']},{'orphan','uninstalled'})


class PairedCandidateTests(unittest.TestCase):
    def test_release_rejects_missing_or_skipped_native_services_and_regressions(self):
        from scripts import release_manifest as release
        for host,channel in release.VARIANTS:
            required=release.REQUIRED_NATIVE | ({'local_flattened_tools','native_chat_completions_lazy_tools',
                                                'native_chat_custom_tools_return_gpt'} if channel=='proxy' else set())
            coverage={'format':1,'kind':'native-client-with-synthetic-services','platform':host,
                      'native':{name:'passed' for name in required},
                      'regressions':{name:'passed' for name in release.REQUIRED_REGRESSIONS | release.PLATFORM_REGRESSIONS[host]}}
            release.validate_coverage(coverage,channel,host)
            with self.assertRaises(ValueError):
                release.validate_coverage(coverage,channel,'windows' if host=='macos' else 'macos')
            for section in ('native','regressions'):
                for name in list(coverage[section]):
                    for value in ('skipped','failed',None):
                        changed=copy.deepcopy(coverage)
                        changed[section][name]=value
                        with self.assertRaises(ValueError):release.validate_coverage(changed,channel,host)

    def test_both_editions_share_one_core_without_changing_checkout_or_feed(self):
        import subprocess
        from scripts import release_manifest as release
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)/'repo';root.mkdir()
            def git(*args):return subprocess.check_output(['git',*args],cwd=root,text=True,**u.background_options()).strip()
            git('init','-q');git('config','user.name','Release Test');git('config','user.email','test@example.invalid')
            (root/'src/tests').mkdir(parents=True)
            for name in ('patch.py','monitor.py','updater.py','update_worker.py','model_sync.py','common.py','tests/test_stub.py'):
                (root/'src'/name).write_text('# shared source\n')
            for name in ('patch.py','monitor.py','README.md','README.en.md'):(root/name).write_text('version 1.0.0\n')
            bases={}
            for channel in ('ds','proxy'):
                metadata={'edition':channel,'branch':u.CHANNELS[channel],'version':'1.0.0','architecture':'official-client-v1'}
                (root/'src/edition.json').write_text(json.dumps(metadata))
                (root/'update.json').write_text(json.dumps({**metadata,'sequence':7}))
                git('add','.');git('commit','-qm',channel);bases[channel]=git('rev-parse','HEAD')
            before=(root/'update.json').read_bytes();head=git('rev-parse','HEAD')
            with mock.object(release,'ROOT',root):
                candidate=release.prepare_pair(head,Path(temp)/'candidate',{'ds':'1.1.0','proxy':'2.0.0-proxy.1'},bases=bases)
            self.assertEqual(git('rev-parse','HEAD'),head)
            self.assertEqual((root/'update.json').read_bytes(),before)
            self.assertEqual(candidate['editions']['ds']['release']['core_sha256'],candidate['editions']['proxy']['release']['core_sha256'])
            self.assertEqual(candidate['editions']['ds']['release']['sequence'],8)
            self.assertEqual(candidate['editions']['proxy']['release']['edition'],'proxy')
            with self.assertRaisesRegex(ValueError,'Missing validation'):release.require_receipts(candidate,Path(temp)/'proof')
            candidate['editions']['ds']['release']['files']['common.py']='0'*64
            self.assertNotEqual(candidate['id'],release.candidate_id(candidate))
