"""Real isolated kegs: execute saved jobs after replacing opt and removing old keg."""
from __future__ import annotations

import datetime
import json
import os
from pathlib import Path
import plistlib
import shlex
import shutil
import subprocess
import tempfile
import unittest
import venv

import support


class HomebrewKegIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='paperdrop-kegs-')
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.home = self.root / 'home'
        self.home.mkdir()
        self.prefix = self.root / 'homebrew'
        self.opt = self.prefix / 'opt' / 'paperdrop'
        self.opt.parent.mkdir(parents=True)
        self.old = self.prefix / 'Cellar' / 'paperdrop' / 'old'
        self.new = self.prefix / 'Cellar' / 'paperdrop' / 'new'
        config, self.config_path = support.write_config(self.home, 'https://example.invalid/desk.json')
        from paperdrop.cache import Cache
        from paperdrop.state import State
        from paperdrop.util import sha256_hex
        image = support.make_image()
        cached = Cache(config['cache_dir']).publish(image, '.jpg')
        today = datetime.datetime.now().astimezone().date().isoformat()
        State(config['state_file']).record_success(
            local_date=today, source_date=today, source_url=config['source']['url'],
            image_sha256=sha256_hex(image), cache_path=str(cached), identity='local-fixture')
        self.state_path = Path(config['state_file'])
        self.cached = cached
        self.create_keg(self.old, 'old')
        self.opt.symlink_to(self.old, target_is_directory=True)
        poison = self.root / 'poison'
        (poison / 'paperdrop').mkdir(parents=True)
        self.hijacked = self.root / 'hijacked'
        (poison / 'paperdrop' / '__init__.py').write_text(
            'from pathlib import Path\nPath(%r).write_text("hijacked")\n'
            'raise RuntimeError("PYTHONPATH hijacked PaperDrop")\n' % str(self.hijacked))
        self.env = {'HOME': str(self.home), 'PATH': str(self.prefix / 'bin') + os.pathsep + os.defpath,
                    'DISPLAY': ':fixture', 'PYTHONDONTWRITEBYTECODE': '1',
                    'PYTHONPATH': str(poison), 'HOMEBREW_PREFIX': '/spoofed',
                    'PYTHONHOME': '/spoofed-pythonhome'}

    def create_keg(self, keg, identity):
        import paperdrop
        import PIL
        environment = keg / 'libexec'
        venv.EnvBuilder(with_pip=False, symlinks=True).create(str(environment))
        python = environment / 'bin' / 'python'
        site = Path(subprocess.check_output(
            [str(python), '-I', '-B', '-c', 'import sysconfig; print(sysconfig.get_path("purelib"))'],
            text=True).strip())
        package = site / 'paperdrop'
        shutil.copytree(Path(paperdrop.__file__).parent, package,
                        ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
        with (package / '__init__.py').open('a', encoding='utf-8') as stream:
            stream.write('\n__brew_test_keg__ = %r\n' % identity)
        (package / '_homebrew.py').write_text('OPT_PREFIX = %r\n' % str(self.opt))
        # Use the actual installed dependency bytes, not a synthesized PIL module.
        pillow_site = Path(PIL.__file__).parent.parent
        for path in pillow_site.iterdir():
            if path.name == 'PIL' or path.name.lower() in ('pillow.libs', 'pillow.dylibs'):
                shutil.copytree(path, site / path.name,
                                ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
        bindir = keg / 'bin'
        bindir.mkdir()
        helper = bindir / 'xfconf-query'
        helper.write_text('#!/bin/sh\ncase " $* " in\n'
                          '  *" -l "*) printf "%s\\n" '
                          '"/backdrop/screen0/monitorFixture/workspace0/last-image";;\n'
                          '  *) printf "%s\\n" ' + shlex.quote(str(self.cached)) + ';;\nesac\n')
        helper.chmod(0o700)

    def run_python(self, argv, env=None):
        result = subprocess.run(argv, env=env or self.env, cwd=str(self.home),
                                text=True, capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(self.hijacked.exists(), 'inherited PYTHONPATH was executed')
        return result

    def test_saved_launchd_and_systemd_commands_survive_upgrade_cleanup_and_pythonpath(self):
        # Generate actual files using the interpreter inside the old keg; all OS
        # registration commands go through injected runners, never host services.
        generator = '''import json,pathlib,subprocess
from unittest.mock import patch
from paperdrop.config import load_config
from paperdrop.paths import Paths
from paperdrop.platforms.macos import MacOS
from paperdrop.platforms.xfce import Xfce
config, config_file = load_config(CONFIG)
paths = Paths.from_config(config, config_file=config_file)
runner = lambda argv, env=None: subprocess.CompletedProcess(argv, 0, stdout='', stderr='')
with patch('paperdrop.platforms.macos.shutil.which', return_value='/fixture/launchctl'):
 mac = MacOS(config=config, home=HOME, runner=runner)
 mac.install(paths, '07:35')
xfce = Xfce(config=config, home=HOME, runner=runner, systemctl='/fixture/systemctl')
xfce.install(paths, '07:35')
'''.replace('CONFIG', repr(str(self.config_path))).replace('HOME', repr(str(self.home)))
        self.run_python([str(self.old / 'libexec/bin/python'), '-I', '-B', '-c', generator])
        apply_file = self.home / 'Library/LaunchAgents/dev.paperdrop.apply.plist'
        watch_file = self.home / 'Library/LaunchAgents/dev.paperdrop.watch.plist'
        service_file = self.home / '.config/systemd/user/paperdrop.service'
        payload = plistlib.loads(apply_file.read_bytes())
        watch = plistlib.loads(watch_file.read_bytes())
        service = service_file.read_text()
        systemd_argv = shlex.split(next(line.partition('=')[2] for line in service.splitlines()
                                        if line.startswith('ExecStart=')))
        systemd_env = dict(line.partition('=')[2].split('=', 1) for line in service.splitlines()
                           if line.startswith('Environment='))
        saved_jobs = [(payload['ProgramArguments'], payload.get('EnvironmentVariables', {})),
                      (systemd_argv, systemd_env)]
        for argv, env in saved_jobs:
            self.assertEqual(argv[:4], [str(self.opt / 'libexec/bin/python'), '-I', '-m', 'paperdrop'])
            self.assertLess(argv.index('--config'), argv.index('run'))
            self.assertNotIn('PYTHONPATH', env)
            self.assertNotIn('Cellar', repr((argv, env)))
        self.assertEqual(watch['ProgramArguments'][:4], saved_jobs[0][0][:4])
        self.assertEqual(watch['ProgramArguments'][-1], 'watch')
        before_files = {p: p.read_bytes() for p in (apply_file, watch_file, service_file)}
        # Execute the exact saved jobs before AND after physically removing old.
        for stage in ('old', 'new'):
            if stage == 'new':
                self.create_keg(self.new, 'new')
                self.opt.unlink()
                self.opt.symlink_to(self.new, target_is_directory=True)
                shutil.rmtree(self.old)
                self.assertFalse(self.old.exists())
            for argv, scheduled_env in saved_jobs:
                self.state_path.write_text(json.dumps(dict(json.loads(self.state_path.read_text()),
                                                           last_attempt=None)))
                env = dict(self.env, **scheduled_env)
                result = self.run_python(argv, env)
                self.assertIn('unchanged:', result.stdout)
                self.assertIs(json.loads(self.state_path.read_text())['last_attempt']['ok'], True)
                # Re-use the recorded interpreter/options, not a fresh resolver.
                probe = argv[:2] + ['-c', 'import json,paperdrop; print(json.dumps('
                         '{"keg":paperdrop.__brew_test_keg__,"module":paperdrop.__file__}))']
                observed = json.loads(self.run_python(probe, env).stdout)
                self.assertEqual(observed['keg'], stage)
                self.assertIn(str(self.old if stage == 'old' else self.new),
                              str(Path(observed['module']).resolve()))
        self.assertEqual(before_files, {p: p.read_bytes() for p in before_files})
        self.assertFalse(self.hijacked.exists())


if __name__ == '__main__':
    unittest.main()
