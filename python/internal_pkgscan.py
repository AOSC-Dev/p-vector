import os
import json
import hashlib
import subprocess

import deb822

class PkgInfoWrapper(object):
    control = None
    filename = ''
    p = None

    def __init__(self, p):
        self.control = deb822.SortPackages(deb822.Packages(p['control']))
        self.p = p


def scan(path: str):
    result = subprocess.check_output(
        [os.path.dirname(__file__) + '/pkgscan_cli', path],
        stdin=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return PkgInfoWrapper(json.loads(result.decode('utf-8')))


def size_sha256_fp(f):
    result = hashlib.new('sha256')
    size = 0
    while True:
        block = f.read(8192)
        if not block:
            break
        size += len(block)
        result.update(block)
    return size, result.hexdigest()


def sha256_file(path: str):
    with open(path, 'rb') as f:
        return size_sha256_fp(f)[1]
