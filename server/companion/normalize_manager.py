"""Prepare a Youmi-named JAR while preserving the customized production entries."""

import argparse
import copy
import hashlib
import io
import json
from pathlib import Path
import struct
import sys
import tarfile
import zipfile


PREFIX = 'BOOT-INF/classes/xiaozhi/modules/device/'
PREIMAGES = {
    'BOOT-INF/classes/xiaozhi/modules/security/config/ShiroConfig.class': 'b0b58ad884f4e48bf3b27c8a88d8553b9f926c3c3bf1cf1d0c74c944399d713a',
    PREFIX + 'controller/MiaDeviceAuthorizationController.class': 'ecdf2c14899c30610b76a5c1263a170a8e83ad862c504e8d6e69442d32e3ab51',
    PREFIX + 'service/impl/MiaDeviceAuthorizationService.class': 'e94589af3cab1b2caa0305bc3958130c0dbb836f144c4530e426ac72e6fd71eb',
    PREFIX + 'service/impl/MiaDeviceAuthorizationService$Binding.class': '893f6e4a8e7e75112a855e2978a15ccb9be3910d88ac00e8372e480645514262',
    PREFIX + 'controller/MiaCompanionManagementController.class': '4460026c5ad8f1c987343e4df9f894348146d04450c6fcde6fc248304b27b853',
}
RENAMES = {'MiaDeviceAuthorizationController': 'YoumiDeviceAuthorizationController',
           'MiaDeviceAuthorizationService': 'YoumiDeviceAuthorizationService',
           'MiaCompanionManagementController': 'YoumiCompanionManagementController',
           'MiaChatAuthorizationService': 'YoumiChatAuthorizationService'}


def renamed(name):
    for old, new in RENAMES.items():
        name = name.replace(old, new)
    return name


def class_names(raw):
    """Rewrite only the listed own type names in UTF8 constant-pool entries."""
    if raw[:4] != b'\xca\xfe\xba\xbe':
        raise ValueError('Invalid class file')
    count = struct.unpack_from('>H', raw, 8)[0]
    result = bytearray(raw[:10])
    offset, index = 10, 1
    sizes = {3: 4, 4: 4, 5: 8, 6: 8, 7: 2, 8: 2, 9: 4, 10: 4, 11: 4,
             12: 4, 15: 3, 16: 2, 17: 4, 18: 4, 19: 2, 20: 2}
    while index < count:
        tag = raw[offset]
        result.append(tag)
        offset += 1
        if tag == 1:
            size = struct.unpack_from('>H', raw, offset)[0]
            value = raw[offset + 2:offset + 2 + size]
            for old, new in RENAMES.items():
                value = value.replace(old.encode(), new.encode())
            result.extend(struct.pack('>H', len(value)))
            result.extend(value)
            offset += size + 2
        else:
            if tag not in sizes:
                raise ValueError('Unsupported constant-pool entry')
            size = sizes[tag]
            result.extend(raw[offset:offset + size])
            offset += size
            if tag in {5, 6}:
                index += 1
        index += 1
    result.extend(raw[offset:])
    return bytes(result)


def prepare(original, classes, output):
    if output.exists():
        raise ValueError('Candidate output must be new')
    changes = {}
    with zipfile.ZipFile(io.BytesIO(original)) as before:
        entries = before.infolist()
        if len({entry.filename for entry in entries}) != len(entries):
            raise ValueError('Duplicate original entries')
        for name, expected in PREIMAGES.items():
            if hashlib.sha256(before.read(name)).hexdigest() != expected:
                raise ValueError('Unreviewed manager preimage: ' + name)
        replacements = {}
        for name in PREIMAGES:
            target = renamed(name)
            if target != name and target in before.namelist():
                raise ValueError('Mixed legacy/Youmi runtime')
            raw = (classes / target.removeprefix('BOOT-INF/classes/')).read_bytes()
            if raw[:4] != b'\xca\xfe\xba\xbe' or int.from_bytes(raw[6:8], 'big') != 65:
                raise ValueError('Expected tested Java21 class')
            replacements[name] = raw
        if any(e.filename.startswith('META-INF/') and e.filename.upper().endswith(('.RSA', '.DSA', '.EC')) for e in entries):
            raise ValueError('Signed outer archive requires a signing workflow')
        prefix = original[:min(e.header_offset for e in entries)]
        with output.open('xb') as stream:
            stream.write(prefix)
            with zipfile.ZipFile(stream, 'w') as after:
                after.comment = before.comment
                for entry in entries:
                    raw = before.read(entry.filename)
                    updated = replacements.get(entry.filename, raw)
                    if entry.filename.startswith('BOOT-INF/classes/') and entry.filename.endswith('.class'):
                        updated = class_names(updated)
                    info = copy.copy(entry)
                    info.filename = renamed(entry.filename)
                    after.writestr(info, updated)
                    if info.filename != entry.filename or updated != raw:
                        changes[entry.filename] = {'target': info.filename, 'sha256': hashlib.sha256(updated).hexdigest()}
        with zipfile.ZipFile(output) as after:
            if set(after.namelist()) != {renamed(e.filename) for e in entries} or after.testzip() is not None:
                raise ValueError('Candidate archive verification failed')
            for entry in entries:
                if entry.filename not in changes and after.read(entry.filename) != before.read(entry.filename):
                    raise ValueError('Unrelated archive entry changed')
    return {'changed_entries': changes, 'candidate_sha256': hashlib.sha256(output.read_bytes()).hexdigest()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('classes', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    # docker cp <container>:/app/xiaozhi-esp32-api.jar - supplies a tar stream;
    # no original JAR copy or production-data backup is created on disk.
    with tarfile.open(fileobj=sys.stdin.buffer, mode='r|') as incoming:
        member = next((item for item in incoming if item.isfile()), None)
        if member is None or Path(member.name).name != 'xiaozhi-esp32-api.jar' or member.size > 256 * 1024 * 1024:
            raise ValueError('Unexpected source JAR')
        original = incoming.extractfile(member).read()
    print(json.dumps(prepare(original, args.classes, args.output), indent=2))


if __name__ == '__main__':
    main()
