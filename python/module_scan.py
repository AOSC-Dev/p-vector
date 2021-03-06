import os
from pathlib import PosixPath

import logging
import binascii
import functools
import urllib.parse
import multiprocessing.dummy
from subprocess import CalledProcessError

import module_ipc
import internal_db
import internal_pkgscan
import internal_dpkg_version

logger_scan = logging.getLogger('SCAN')

FILETYPES = {
    0o100000: 'reg',
    0o120000: 'lnk',
    0o140000: 'sock',
    0o020000: 'chr',
    0o060000: 'blk',
    0o040000: 'dir',
    0o010000: 'fifo',
}

def split_soname(s: str):
    spl = s.rsplit('.so', 1)
    if len(spl) == 1:
        return s, ''
    else:
        return spl[0]+'.so', spl[1]

def parse_debname(s: str):
    basename = urllib.parse.unquote(os.path.splitext(os.path.basename(s))[0])
    package, other = basename.split('_', 1)
    version, arch = other.rsplit('_', 1)
    return package, version, arch

def scan_deb(args):
    # fullpath: str, filename: str, size: int, mtime: int
    # Scan it.
    fullpath, filename, size, mtime = args
    try:
        p = internal_pkgscan.scan(fullpath)
    except CalledProcessError as e:
        if e.returncode in (1, 2):
            logger_scan.error('%s is corrupted, status: %d', fullpath, e.returncode)
            package, version, arch = parse_debname(filename)
            pkginfo = {
                'package': package, 'version': version, 'architecture': arch,
                'filename': filename, 'size': size, 'mtime': mtime,
            }
            try:
                pkginfo['sha256'] = internal_pkgscan.sha256_file(fullpath)
            except Exception:
                logger_scan.exception('cannot access %s', fullpath)
            return pkginfo, {}, [], []
        raise
    # Make a new document
    pkginfo = {
        'package': p.control['Package'],
        'version': p.control['Version'],
        '_vercomp': internal_dpkg_version.comparable_ver(p.control['Version']),
        'architecture': p.control['Architecture'],
        'filename': filename,
        'size': size,
        'sha256': binascii.b2a_hex(bytes(p.p['hash_value'])).decode('ascii'),
        'mtime': mtime,
        'debtime': p.p['time'],
        'section': p.control.get('Section'),
        'installed_size': p.control['Installed-Size'],
        'maintainer': p.control['Maintainer'],
        'description': p.control['Description'],
    }
    depinfo = {k:p.control[k] for k in ('Depends', 'Pre-Depends', 'Recommends',
        'Suggests', 'Enhances', 'Breaks', 'Conflicts', 'Provides', 'Replaces')
        if k in p.control}
    sodeps = []
    for row in p.p['so_provides']:
        sodeps.append((0,) + split_soname(row))
    for row in p.p['so_depends']:
        sodeps.append((1,) + split_soname(row))
    files = []
    for row in p.p['files']:
        path, name = os.path.split(os.path.normpath(
            os.path.join('/', row['path'])))
        files.append((
            path.lstrip('/'), name, row['size'],
            FILETYPES.get(row['type'], str(row['type'])),
            row['perm'], row['uid'], row['gid'], row['uname'], row['gname']
        ))
    return pkginfo, depinfo, sodeps, files

dpkg_vercomp_key = functools.cmp_to_key(
    internal_dpkg_version.dpkg_version_compare)

def scan_dir(db, base_dir: str, branch: str, component: str, branch_idx: int):
    pool_path = PosixPath(base_dir).joinpath('pool')
    search_path = pool_path.joinpath(branch).joinpath(component)
    compname = '%s-%s' % (branch, component)
    comppath = '%s/%s' % (branch, component)
    cur = db.cursor()
    cur.execute("""SELECT p.package, p.version, p.repo, p.architecture,
          p.filename, p.size, p.mtime, p.sha256
        FROM pv_packages p
        INNER JOIN pv_repos r ON p.repo=r.name WHERE r.path=%s
        UNION ALL
        SELECT p.package, p.version, p.repo, p.architecture,
          p.filename, p.size, p.mtime, p.sha256
        FROM pv_package_duplicate p
        INNER JOIN pv_repos r ON p.repo=r.name WHERE r.path=%s""",
        (comppath, comppath))
    dup_pkgs = set()
    ignore_files = set()
    modified_repo = set()
    del_list = []
    # For each package/version/architecture we already know in the DB:
    for package, version, repopath, architecture, filename, size, mtime, sha256 in cur:
        fullpath = PosixPath(base_dir).joinpath(filename)
        if fullpath.is_file():
            # If a package with the same name exists:
            stat = fullpath.stat()
            sfullpath = str(fullpath)
            if size == stat.st_size and (mtime == int(stat.st_mtime) or
                # Ignore if the file isn't changed
                internal_pkgscan.sha256_file(sfullpath) == sha256):
                ignore_files.add(sfullpath)
            else:
                # Consider the new file to be a duplicate and replace the old one
                dup_pkgs.add(filename)
                del_list.append((filename, package, version, repopath))
        else:
            # If the package has been deleted
            del_list.append((filename, package, version, repopath))
            logger_scan.info('CLEAN  %s', filename)
            module_ipc.publish_change(
                compname, package, architecture, 'delete', version, '')
    # For each package/version/arch/repo to be deleted:
    for row in del_list:
        cur.execute("DELETE FROM pv_packages WHERE filename=%s", (row[0],))
        modified_repo.add(row[1:][-1])
    # Check if there are any new files added. Recursively scan the pool dir and take notes of
    # what we haven't seen yet.
    check_list = []
    for fullpath in search_path.rglob('*.deb'):
        if not fullpath.is_file():
            continue
        stat = fullpath.stat()
        sfullpath = str(fullpath)
        if sfullpath in ignore_files:
            continue
        check_list.append((sfullpath, str(fullpath.relative_to(base_dir)),
                           stat.st_size, int(stat.st_mtime)))
    del ignore_files
    with multiprocessing.dummy.Pool(max(1, os.cpu_count() - 1)) as mpool:
        for pkginfo, depinfo, sodeps, files in mpool.imap_unordered(scan_deb, check_list, 5):
            realname = pkginfo['architecture']
            validdeb = ('debtime' in pkginfo)
            if realname == 'all':
                realname = 'noarch'
            if component != 'main':
                realname = component + '-' + realname
            repo = '%s/%s' % (realname, branch)
            cur.execute("INSERT INTO pv_repos VALUES (%s,%s,%s,%s,%s,%s,%s,now()) "
                "ON CONFLICT DO NOTHING",
                (repo, realname, comppath, branch_idx,
                branch, component, pkginfo['architecture']))
            modified_repo.add(repo)
            pkginfo['repo'] = repo
            dbkey = (pkginfo['package'], pkginfo['version'], repo)
            if pkginfo['filename'] in dup_pkgs:
                if validdeb:
                    logger_scan.info('UPDATE %s', pkginfo['filename'])
                    module_ipc.publish_change(
                        compname, pkginfo['package'], pkginfo['architecture'],
                        'overwrite', pkginfo['version'], pkginfo['version']
                    )
            else:
                cur.execute("SELECT version, filename FROM pv_packages "
                    "WHERE package=%s AND repo=%s", (pkginfo['package'], repo))
                results = cur.fetchall()
                if results:
                    oldver = max(results, key=lambda x: dpkg_vercomp_key(x[0]))
                    vercomp = internal_dpkg_version.dpkg_version_compare(
                        oldver[0], pkginfo['version'])
                    if vercomp == -1:
                        if validdeb:
                            logger_scan.info('NEWER  %s %s %s >> %s',
                                pkginfo['architecture'], pkginfo['package'],
                                pkginfo['version'], oldver[0])
                            module_ipc.publish_change(
                                compname, pkginfo['package'],
                                pkginfo['architecture'], 'upgrade',
                                oldver[0], pkginfo['version']
                            )
                    elif vercomp:
                        logger_scan.warning('OLD    %s %s %s',
                            pkginfo['architecture'], pkginfo['package'],
                            pkginfo['version'])
                    else:
                        cur.execute("DELETE FROM pv_package_sodep "
                            "WHERE package=%s AND version=%s AND repo=%s", dbkey)
                        cur.execute("DELETE FROM pv_package_files "
                            "WHERE package=%s AND version=%s AND repo=%s", dbkey)
                        cur.execute("DELETE FROM pv_package_dependencies "
                            "WHERE package=%s AND version=%s AND repo=%s", dbkey)
                        cur.execute("DELETE FROM pv_package_duplicate "
                            "WHERE package=%s AND version=%s AND repo=%s", dbkey)
                        cur.execute("INSERT INTO pv_package_duplicate "
                            "SELECT * FROM pv_packages WHERE filename=%s",
                            (oldver[1],))
                        cur.execute("DELETE FROM pv_packages "
                            "WHERE package=%s AND version=%s AND repo=%s", dbkey)
                        logger_scan.error('DUP    %s == %s',
                            oldver[1], pkginfo['filename'])
                elif validdeb:
                    logger_scan.info('NEW    %s %s %s', pkginfo['architecture'],
                        pkginfo['package'], pkginfo['version'])
                    module_ipc.publish_change(
                        compname, pkginfo['package'], pkginfo['architecture'],
                        'new', '', pkginfo['version']
                    )
            keys, qms, vals = internal_db.make_insert(pkginfo)
            cur.execute("INSERT INTO pv_packages (%s) VALUES (%s)" %
                (keys, qms), vals)
            for row in depinfo.items():
                cur.execute("INSERT INTO pv_package_dependencies "
                    "VALUES (%s,%s,%s,%s,%s) "
                    "ON CONFLICT ON CONSTRAINT pv_package_dependencies_pkey "
                    "DO UPDATE SET value = %s",
                    dbkey + row + (row[1],))
            for row in sodeps:
                cur.execute("INSERT INTO pv_package_sodep VALUES "
                    "(%s,%s,%s,%s,%s,%s)", dbkey + row)
            for row in files:
                cur.execute("INSERT INTO pv_package_files VALUES "
                    "(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)", dbkey + row)
    for repo in modified_repo:
        cur.execute("UPDATE pv_repos SET mtime=now() WHERE name=%s", (repo,))

def table_mtime(db):
    cur = db.cursor()
    cur.execute("SELECT coalesce(extract(epoch FROM max(mtime)), 0) FROM pv_repos")
    result = cur.fetchone()[0]
    cur.close()
    return result

def scan(db, base_dir: str, branch_list: list):
    pool_dir = base_dir + '/pool'
    internal_db.init_db(db)
    lastmtime = table_mtime(db)
    for i in PosixPath(pool_dir).iterdir():
        if not i.is_dir():
            continue
        branch_name = i.name
        try:
            branch_idx = branch_list.index(branch_name)
            branch_list.remove(branch_name)
        except ValueError as e:
            logger_scan.warning('Skipping %s as it is not specified in configuration', branch_name)
            continue
        logger_scan.info('Branch: %s', branch_name)
        for j in PosixPath(pool_dir).joinpath(branch_name).iterdir():
            if not j.is_dir():
                continue
            component_name = j.name
            logger_scan.info('==== %s-%s ====', branch_name, component_name)
            try:
                scan_dir(db, base_dir, branch_name, component_name, branch_idx)
            finally:
                db.commit()
    if branch_list:
        logger_scan.warning("Branches skipped as they are missing on disk: %s", " ".join(branch_list))
    refresh = (table_mtime(db) > lastmtime)
    internal_db.init_index(db, refresh)
    #db.execute('ANALYZE')
