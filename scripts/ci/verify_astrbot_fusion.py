"""Reproducible checks used by the same workflow on GitHub or local nektos/act."""

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import urllib.request


ROOT = Path(__file__).resolve().parents[2]
PINS = json.loads((ROOT / 'deploy/astrbot-fusion/versions.json').read_text())


def run(*command, cwd=ROOT, env=None):
    subprocess.run([str(part) for part in command], cwd=cwd, env=env, check=True)


def source(name, cache):
    pin = PINS[name]
    folder = cache / ('astrbot_plugin_meme_manager' if name == 'meme' else name)
    marker = folder / '.youmi-source-sha'
    if folder.exists():
        if marker.exists() and marker.read_text().strip() == pin['sha']:
            return folder
        if (folder / '.git').exists():
            actual = subprocess.check_output(['git', '-C', str(folder), 'rev-parse', 'HEAD'], text=True).strip()
            if actual == pin['sha']:
                return folder
        raise SystemExit('Source directory exists without matching provenance: ' + str(folder))
    folder.mkdir(parents=True)
    # Exact commit archive over verified HTTPS; extract only regular repository
    # files, never symlinks/absolute paths or runtime credentials.
    url = pin['repo'].replace('https://github.com/', 'https://api.github.com/repos/') + '/tarball/' + pin['sha']
    try:
        with urllib.request.urlopen(url, timeout=60) as response, tarfile.open(fileobj=response, mode='r|gz') as archive:
            for item in archive:
                parts = Path(item.name).parts[1:]
                if not item.isfile() or not parts or '..' in parts:
                    continue
                if (name == 'xiaozhi' and parts[:2] not in [('main', 'manager-api'), ('main', 'xiaozhi-server')]
                        and tuple(parts) != ('main', 'manager-web', 'src', 'views', 'roleConfig.vue')):
                    continue
                path = folder.joinpath(*parts)
                path.parent.mkdir(parents=True, exist_ok=True)
                with archive.extractfile(item) as incoming, path.open('wb') as output:
                    shutil.copyfileobj(incoming, output)
        marker.write_text(pin['sha'] + '\n')
    except BaseException:
        shutil.rmtree(folder)
        raise
    return folder


def backend(cache):
    fixtures = cache / 'fixtures'
    fixtures.mkdir(exist_ok=True)
    legacy = fixtures / 'memory.py'
    if not legacy.exists():
        url = 'https://raw.githubusercontent.com/bboytang/Youmi/a30a5081bfa3c08e7387b9f5d9be77bd957e8175/server/companion/memory.py'
        legacy.write_bytes(urllib.request.urlopen(url, timeout=30).read())
    upstream = source('xiaozhi', cache)
    env = os.environ | {'MIA_LEGACY_MEMORY_SOURCE': str(legacy), 'MIA_MUSIC_TEST_SOURCE': str(
        upstream / 'main/xiaozhi-server/plugins_func/functions/play_music.py'), 'YOUMI_TTS_TEST_SOURCE': str(
        upstream / 'main/xiaozhi-server/core/providers/tts/huoshan_double_stream.py')}
    run(sys.executable, '-m', 'unittest', 'discover', '-s', 'server/tests', '-v', env=env)
    run(sys.executable, '-m', 'unittest', 'discover', '-s', 'scripts/tests', '-v')
    run(sys.executable, '-m', 'unittest', 'discover', '-s', 'scripts/ci/tests', '-v')
    run('docker', 'compose', '--env-file', 'deploy/astrbot-fusion/.env.example', '-f',
        'deploy/astrbot-fusion/compose.yaml', 'config', '--quiet')


def connector(cache):
    astrbot, meme = source('astrbot', cache), source('meme', cache)
    env = os.environ | {'ASTRBOT_ROOT': str(cache / 'runtime'),
        'ASTRBOT_DISABLE_METRICS': '1',
        'PYTHONPATH': os.pathsep.join([str(ROOT), str(astrbot), str(cache)])}
    run(sys.executable, '-m', 'pytest', 'integrations/tests', '-v', env=env)
    run(sys.executable, '-m', 'pytest', str(meme / 'tests'), '-q', '-rs', env=env)


def manager(cache, *, legacy=False):
    upstream = source('xiaozhi', cache)
    role = upstream / 'main/manager-web/src/views/roleConfig.vue'
    if not role.exists():
        role.parent.mkdir(parents=True, exist_ok=True)
        role.write_bytes(urllib.request.urlopen('https://raw.githubusercontent.com/xinnan-tech/xiaozhi-esp32-server/'
            + PINS['xiaozhi']['sha'] + '/main/manager-web/src/views/roleConfig.vue', timeout=30).read())
    candidate = cache / ('manager-legacy-candidate' if legacy else 'manager-candidate')
    if candidate.exists():
        shutil.rmtree(candidate)
    shutil.copytree(upstream, candidate)
    run(sys.executable, '-m', 'server.companion.install_youmi', candidate, '--apply')
    run(sys.executable, '-m', 'server.companion.install_identity', candidate, '--apply')
    run('git', 'apply', str(ROOT / 'server/companion/manager_tests/sensitive-data-expectation.patch'), cwd=candidate)
    tests = candidate / 'main/manager-api/src/test/java/xiaozhi/modules/device'
    tests.mkdir(parents=True, exist_ok=True)
    for path in (ROOT / 'server/companion/manager_tests').glob('YoumiPrivate*.java'):
        shutil.copy2(path, tests / path.name)
    shutil.copy2(ROOT / 'server/companion/manager_tests/YoumiBindingAuthorizationTest.java', tests)
    if legacy:
        run(sys.executable, '-m', 'server.companion.install_astrbot_manager', 'legacy-source', candidate, '--apply')
    tls = cache / ('tls-legacy' if legacy else 'tls')
    tls.mkdir(exist_ok=True)
    ca, certificate, key = tls / 'ca.pem', tls / 'server.pem', tls / 'server.key'
    run('openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '2', '-keyout', tls / 'ca.key',
        '-out', ca, '-subj', '/CN=Youmi isolated act CA', '-addext', 'basicConstraints=critical,CA:TRUE')
    run('openssl', 'req', '-newkey', 'rsa:2048', '-nodes', '-keyout', key, '-out', tls / 'server.csr', '-subj', '/CN=localhost')
    (tls / 'server.ext').write_text('subjectAltName=DNS:localhost,IP:127.0.0.1\nextendedKeyUsage=serverAuth\n')
    run('openssl', 'x509', '-req', '-days', '2', '-in', tls / 'server.csr', '-CA', ca, '-CAkey', tls / 'ca.key',
        '-CAcreateserial', '-extfile', tls / 'server.ext', '-out', certificate)
    run('openssl', 'pkcs12', '-export', '-out', tls / 'server.p12', '-inkey', key, '-in', certificate,
        '-certfile', ca, '-passout', 'pass:youmi-ci-only')
    # A private test truststore, never modify the user's system/production trust.
    java_home = Path(subprocess.check_output(['java', '-XshowSettings:properties', '-version'],
        stderr=subprocess.STDOUT, text=True).split('java.home = ', 1)[1].splitlines()[0].strip())
    shutil.copy2(java_home / 'lib/security/cacerts', tls / 'truststore')
    run('keytool', '-importcert', '-noprompt', '-alias', 'youmi-act-ci', '-file', ca,
        '-keystore', tls / 'truststore', '-storepass', 'changeit')
    import certifi
    certifi_path = Path(certifi.where())
    original_trust = certifi_path.read_bytes()
    env = os.environ | {'MIA_SCOPE_KEYSTORE': 'file:' + str(tls / 'server.p12'), 'MIA_SCOPE_CA': str(ca),
        'MIA_TLS_CERT_FILE': str(certificate), 'MIA_TLS_KEY_FILE': str(key),
        'MIA_SCOPE_INTEGRATION_SCRIPT': str(ROOT / 'scripts/ci/verify_youmi_scope_integration.py'),
        'MIA_SCOPE_UPSTREAM': str(candidate),
        'JAVA_TOOL_OPTIONS': os.environ.get('JAVA_TOOL_OPTIONS', '') + ' -Djavax.net.ssl.trustStore=' + str(tls / 'truststore')
            + ' -Djavax.net.ssl.trustStorePassword=changeit -Xmx384m'}
    args = ['mvn', '--batch-mode', '-DskipTests=false', '-DfailIfNoTests=true']
    if os.environ.get('YOUMI_MAVEN_REPO'):
        args.append('-Dmaven.repo.local=' + os.environ['YOUMI_MAVEN_REPO'])
    if os.environ.get('YOUMI_MAVEN_SETTINGS'):
        args += ['--settings', os.environ['YOUMI_MAVEN_SETTINGS']]
    # Existing real Scope tests intentionally use httpx(trust_env=False), which
    # reads certifi rather than SSL_CERT_FILE. Only this isolated venv's CA bundle
    # is augmented, then restored; native default SSL must still reject this CA.
    try:
        certifi_path.write_bytes(original_trust + b'\n' + ca.read_bytes())
        run(*args, 'test', cwd=candidate / 'main/manager-api', env=env)
        run(*[arg for arg in args if arg != '-DskipTests=false'], '-DskipTests=true', 'package',
            cwd=candidate / 'main/manager-api', env=env)
        if legacy:
            run(sys.executable, '-m', 'server.companion.install_astrbot_manager', 'package',
                candidate / 'main/manager-api/target/classes', candidate / 'astrbot-manager-patch.zip')
    finally:
        certifi_path.write_bytes(original_trust)


def manager_legacy(cache):
    manager(cache, legacy=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('check', choices=['backend', 'connector', 'manager', 'manager-legacy'])
    args = parser.parse_args()
    cache = Path(os.environ.get('YOUMI_TEST_SOURCE_DIR', str(Path(os.environ.get('RUNNER_TEMP', '/tmp')) / 'youmi-fusion-sources')))
    cache.mkdir(parents=True, exist_ok=True)
    print(json.dumps({'check': args.check, 'source_sha': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
        'act': os.environ.get('ACT') == 'true'}, ensure_ascii=False), flush=True)
    globals()[args.check.replace('-', '_')](cache)


if __name__ == '__main__':
    main()
