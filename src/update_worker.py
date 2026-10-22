"""Stable isolated process interface for staged patch code and model updates."""
from pathlib import Path
import argparse
import json
import sys

import patch as p
import backend


def verify_build(output):
    output=Path(output)
    manifest=json.loads((output/'manifest.json').read_text(encoding='utf-8'))
    if manifest.get('architecture')=='official-client-v1':
        from scripts.test_official_client import run
        manifest.pop('native_probe',None)
        manifest.pop('native_probe_report',None)
        p.save_json(output/'manifest.json',manifest)
        from official_client import ADAPTER_FILES
        p.require(set(manifest.get('runtime_sha256',{})) == set(ADAPTER_FILES),'Native runtime inventory mismatch')
        for name, digest in manifest['runtime_sha256'].items():
            p.require(p.file_sha(output/name) == digest == p.file_sha(p.ROOT/name),
                      'Staged runtime differs from the source being tested: '+name)
        for name, field in [('desktop-models.json','catalog_sha256'),('adapter-profile.json','profile_sha256'),('host-settings.json','host_settings_sha256')]:
            p.require(p.file_sha(output/name) == manifest[field], 'Staged configuration changed: '+name)
        report=run(Path(manifest['source_target']),output/'native-probe',manifest['edition'])
        p.require(report.get('status')=='passed','Native official-client validation failed')
        manifest.update(native_probe='passed',native_probe_report=str(output/'native-probe'))
        p.save_json(output/'manifest.json',manifest)
        return report
    from scripts.test_offline import main as offline
    from scripts.test_reasoning import main as reasoning
    from scripts.test_boot import main as boot
    offline(output)
    reasoning(output)
    if manifest.get('proxy') or manifest.get('model_routes'):
        from scripts.test_context import main as context
        context(output)
    if manifest.get('switching_probe_required'):
        from scripts.test_switching import main as switching
        switching(output)
    if manifest.get('tool_loading_probe_required'):
        from scripts.test_tools import main as tools
        tools(output)
    boot(output)


def finish_native_update(directory, manifest, progress=None):
    """Commit independent recovery before old parent code touches its journal."""
    update=manifest.get('update',{})
    package=update.get('package')
    if not package:
        if progress:progress.publish('completed',build=directory,present=True)
        return
    state=update.get('monitor_state_dir') or update.get('progress_state_dir')
    p.require(state and Path(state).is_absolute(),'Native update monitor directory is missing')
    state=Path(state).resolve()
    settings=json.loads((state/'settings.json').read_text(encoding='utf-8'))
    p.require(settings.get('edition')==manifest['edition'],'Native update monitor edition changed')
    from updater import activate_runtime,reconcile_native_activation,NATIVE_RECOVERY
    from monitor import atomic_json
    atomic_json(state/NATIVE_RECOVERY,{'format':1,'edition':manifest['edition'],
        'baseline':str(Path(directory).resolve()),'runtime_digest':update['runtime_digest']})
    atomic_json(state/'activation-pending.json',{'baseline':str(directory),'edition':manifest['edition']})
    settings.update(architecture='official-client-v1',baseline=str(directory),app=manifest['target'])
    atomic_json(state/'settings.json',settings)
    if progress:progress.publish('activating')
    try:
        activate_runtime(Path(package),state,manifest['edition'])
        reconcile_native_activation(state,already_locked=True)
    except Exception:
        # The core install committed. An old parent can release its own cwd and
        # promote; otherwise both durable records survive for the next check.
        return


def execute(action, job, progress=None):
    if action=='identity':
        import updater
        return {**updater.edition(),'architecture':getattr(p,'ARCHITECTURE','legacy'),
                'runtime_digest':updater.runtime_digest(p.ROOT),'files':list(p.SERVICE_RUNTIME_FILES)}
    if action=='sync-models':
        import model_sync
        value=model_sync.synchronize(job['settings'],job['directory'],job['baseline'])
        return {'digest':value['digest'],'warnings':value['warnings']}
    if action=='inspect':
        if job.get('architecture',getattr(p,'ARCHITECTURE',None))=='official-client-v1':
            import official_client
            return official_client.inspect(Path(job['app']))
        value=backend.inspect_source(Path(job['app']))
        return {'compatible':True,'assertions':value['probe']['assertions']}
    if action=='build':
        settings=job['settings'];output=Path(job['output'])
        metadata=dict(job.get('update') or {})
        if settings.get('state_dir'):metadata['monitor_state_dir']=str(Path(settings['state_dir']).resolve())
        if progress:metadata.update(progress.metadata())
        job={**job,'update':metadata}
        if progress:
            progress.publish('building',build=output,present=True)
        if job.get('architecture',getattr(p,'ARCHITECTURE',None))=='official-client-v1':
            import official_client
            preferences={}
            if settings.get('state_dir'):
                path=Path(settings['state_dir'])/'model-preferences.json'
                if path.is_file():preferences=json.loads(path.read_text(encoding='utf-8'))
            official_client.stage(app=Path(job['app']),codex_home=Path(settings['codex_home']),
                output=output,baseline=Path(job['baseline']),edition=settings.get('edition'),
                model_snapshot=job.get('models'),install_target=Path(job['install_target']) if job.get('install_target') else None,
                update_metadata={**(job.get('update') or {}),'architecture':'official-client-v1'},model_preferences=preferences)
            if progress:progress.publish('verifying')
            verify_build(output)
            if progress:
                progress.publish('waiting_for_exit',build=output)
                from update_progress import validated_close
                validated_close(progress)
            if settings.get('state_dir'):
                from monitor import migrate_windows_working_directory
                migrate_windows_working_directory(settings['state_dir'])
            return {'directory':str(output),'status':'prepared','architecture':'official-client-v1'}
        args=argparse.Namespace(app=Path(job['app']),codex_home=Path(settings['codex_home']),output=output,
             compatible=True,baseline=Path(job['baseline']),keychain_account=settings['keychain_account'],
             keychain_service=settings['keychain_service'],node_path=settings.get('node_path'),
             install_target=Path(job['install_target']) if job.get('install_target') else None,
             model_snapshot=job.get('models'),update_metadata=job.get('update'))
        backend.build_compatible(args)
        if progress:progress.publish('verifying')
        verify_build(output)
        if progress:progress.publish('waiting_for_exit',build=output)
        if settings.get('state_dir'):
            from monitor import migrate_windows_working_directory
            migrate_windows_working_directory(settings['state_dir'])
        return {'directory':str(output),'status':'prepared'}
    if action=='install':
        directory=Path(job['directory'])
        manifest=json.loads((directory/'manifest.json').read_text(encoding='utf-8'))
        if progress:progress.publish('installing',build=directory)
        if manifest.get('architecture')=='official-client-v1':
            from official_client_install import install
            install(directory,manifest)
            finish_native_update(directory,manifest,progress)
            return {'directory':str(directory),'status':'installed','architecture':'official-client-v1'}
        directory,manifest=p.load_build(Path(job['directory']))
        backend.install_compatible(directory,manifest)
        if progress:progress.publish('completed',build=directory,present=True)
        return {'directory':str(directory),'status':'installed'}
    raise ValueError('Unknown update worker action')


def main(argv=None):
    args=sys.argv[1:] if argv is None else argv
    action,path=args[0],Path(args[1])
    progress=None
    try:
        job=json.loads(path.read_text(encoding='utf-8'))
        from update_progress import worker_progress
        progress=worker_progress(action,job,path)
        if progress and action=='identity':
            # Periodic identity probes must not erase a failed/completed
            # update or replace the state of an update already in progress.
            if progress.read().get('phase') in ('available','checking'):
                progress.publish('checking',present=True)
            else:
                progress=None
        value=execute(action,job,progress)
        p.save_json(path.with_suffix('.result.json'),value)
        return 0
    except p.AppRunningError:
        if action=='install':
            if progress:progress.publish('waiting_for_exit')
            return 75
        if progress:progress.publish('failed',present=True)
        return 1
    except Exception as error:
        if progress:progress.publish('failed',present=True)
        from monitor import safe_error
        print(safe_error(error),file=sys.stderr)
        return 1


if __name__=='__main__':
    raise SystemExit(main())
