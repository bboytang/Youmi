"""Persist focused real binding results; retain full-suite failures explicitly."""

import argparse
import hashlib
import json
import os
from pathlib import Path
from xml.etree import ElementTree


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reports', type=Path, required=True)
    parser.add_argument('--classes', type=Path, required=True)
    parser.add_argument('--baseline', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    suites = [ElementTree.parse(path).getroot() for path in sorted(args.reports.glob('TEST-*.xml'))]
    binding = next((s for s in suites if s.get('name') == 'xiaozhi.modules.device.YoumiBindingAuthorizationTest'), None)
    private = next((s for s in suites if s.get('name') == 'xiaozhi.modules.device.YoumiPrivateAuthorizationTest'), None)
    transport = next((s for s in suites if s.get('name') == 'xiaozhi.modules.device.YoumiPrivateTransportTest'), None)
    tls = next((s for s in suites if s.get('name') == 'xiaozhi.modules.device.YoumiPrivateTlsTest'), None)
    chat = next((s for s in suites if s.get('name') == 'xiaozhi.modules.device.YoumiPrivateChatTest'), None)
    ota = next((s for s in suites if s.get('name') == 'xiaozhi.modules.device.YoumiPrivateOtaDiscoveryTest'), None)
    counts = {key: sum(int(s.get(key, '0')) for s in suites) for key in ['tests', 'failures', 'errors', 'skipped']}
    failed_cases = [{'suite': s.get('name'), 'case': c.get('name')}
                    for s in suites for c in s.findall('testcase')
                    if c.find('failure') is not None or c.find('error') is not None]
    baseline = json.loads(args.baseline.read_text())
    classes = {}
    for name, expected in baseline['classes'].items():
        actual = hashlib.sha256((args.classes / name).read_bytes()).hexdigest()
        intended_change = name.endswith(('/DeviceServiceImpl.class', '/ShiroConfig.class', '/OTAController.class'))
        classes[name] = {'baseline': expected, 'actual': actual,
                         'intended_change': intended_change,
                         'matches_change_scope': actual != expected if intended_change else actual == expected}
    binding_pass = (binding is not None and int(binding.get('tests', '0')) == 4
                    and all(int(binding.get(key, '0')) == 0 for key in ['failures', 'errors', 'skipped']))
    private_pass = (private is not None and int(private.get('tests', '0')) == 16
                    and private.find("testcase[@name='virtualBridgeUsesIndependentDeviceGrantAndRejectsOtherOwner']") is not None
                    and private.find("testcase[@name='virtualRegistrationDoesNotAcceptCallerOwnedDeviceOrOwnerFields']") is not None
                    and private.find("testcase[@name='physicalHardwareCodeRequiresCurrentOwnerAndUsesOriginalOneTimeExchange']") is not None
                    and private.find("testcase[@name='sixDigitHardwareCodeApprovesOnlyTheRegisteredSecretAndClient']") is not None
                    and all(int(private.get(key, '0')) == 0 for key in ['failures', 'errors', 'skipped']))
    transport_pass = (transport is not None and int(transport.get('tests', '0')) == 1
                      and all(int(transport.get(key, '0')) == 0 for key in ['failures', 'errors', 'skipped']))
    tls_pass = (tls is not None and int(tls.get('tests', '0')) == 1
                and all(int(tls.get(key, '0')) == 0 for key in ['failures', 'errors', 'skipped']))
    chat_pass = (chat is not None and int(chat.get('tests', '0')) == 5
                 and all(int(chat.get(key, '0')) == 0 for key in ['failures', 'errors', 'skipped']))
    ota_pass = (ota is not None and int(ota.get('tests', '0')) == 7
                and all(int(ota.get(key, '0')) == 0 for key in ['failures', 'errors', 'skipped']))
    report = {'code_sha': os.environ.get('GITHUB_SHA'),
              'upstream_sha': '87c6df77b0220ddbd8fc804f984f83f69f3225e5',
              'passport_ota_discovery_pass': ota_pass,
              'binding_authorization_pass': binding_pass, 'private_authorization_pass': private_pass,
              'private_default_transport_pass': transport_pass, 'full_suite': counts,
              'private_tls_transport_pass': tls_pass,
              'private_chat_authorization_pass': chat_pass,
              'full_suite_pass': bool(suites) and counts['failures'] == 0 and counts['errors'] == 0,
              'failed_cases': failed_cases, 'classes': classes,
              'note': 'Manager identity chain only; voice/worker Scope, App migration and public TLS not yet accepted.'}
    args.output.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))
    if not (binding_pass and private_pass and transport_pass and tls_pass and chat_pass and ota_pass) or not all(row['matches_change_scope'] for row in classes.values()):
        raise SystemExit('Binding verification or class scope check failed')


if __name__ == '__main__':
    main()
