
install(FILES
        deb822.py
        internal_db.py
        internal_dpkg_version.py
        internal_pkgscan.py
        module_config.py
        module_gc.py
        module_ipc.py
        module_release.py
        module_scan.py
        module_sync.py
        vercomp.sql
        abbsdb.sql
        pkgissues.sql
        foreignkey.sql
        DESTINATION ${LIBEXEC_PATH})
