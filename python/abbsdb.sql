-- abbs.db

CREATE TABLE IF NOT EXISTS trees (
tid SMALLINT PRIMARY KEY,
name TEXT UNIQUE,
category TEXT,
url TEXT,
mainbranch TEXT
);
CREATE TABLE IF NOT EXISTS packages (
name TEXT PRIMARY KEY,
tree TEXT,
category TEXT,
section TEXT,
pkg_section TEXT,
directory TEXT,
description TEXT
);
CREATE TABLE IF NOT EXISTS package_duplicate (
package TEXT,
tree TEXT,
category TEXT,
section TEXT,
directory TEXT,
UNIQUE (package, tree, category, section, directory)
);
CREATE TABLE IF NOT EXISTS package_versions (
package TEXT,
branch TEXT,
version TEXT,
release TEXT,
epoch TEXT,
commit_time INTEGER,
committer TEXT,
PRIMARY KEY (package, branch)
);
CREATE TABLE IF NOT EXISTS package_spec (
package TEXT,
key TEXT,
value TEXT,
PRIMARY KEY (package, key)
-- FOREIGN KEY(package) REFERENCES packages(name)
);
CREATE TABLE IF NOT EXISTS package_dependencies (
package TEXT,
dependency TEXT,
version TEXT,
relationship TEXT,
PRIMARY KEY (package, dependency, relationship)
-- FOREIGN KEY(package) REFERENCES packages(name)
);
CREATE TABLE IF NOT EXISTS dpkg_repo_stats (
repo TEXT PRIMARY KEY,
packagecnt INTEGER,
ghostcnt INTEGER,
laggingcnt INTEGER,
missingcnt INTEGER
-- FOREIGN KEY(repo) REFERENCES dpkg_repos(name)
);
CREATE INDEX idx_package_dependencies_rev ON package_dependencies (dependency);

CREATE OR REPLACE VIEW v_packages AS
SELECT p.name, p.tree tree, t.category tree_category,
  pv.branch branch, p.category category,
  section, pkg_section, directory, description, version,
  ((CASE WHEN ifnull(epoch, '') = '' THEN ''
    ELSE epoch || ':' END) || version ||
   (CASE WHEN ifnull(release, '') IN ('', '0') THEN ''
    ELSE '-' || release END)) full_version,
  pv.commit_time commit_time, pv.committer committer
FROM packages p
INNER JOIN trees t ON t.name=p.tree
LEFT JOIN package_versions pv
  ON pv.package=p.name AND pv.branch=t.mainbranch;

-- piss.db

CREATE TABLE upstream_status (
package TEXT PRIMARY KEY,
updated INTEGER,
last_try INTEGER,
err TEXT
);
CREATE TABLE package_upstream (
package TEXT PRIMARY KEY,
type TEXT,
version TEXT,
"time" INTEGER,
url TEXT,
tarball TEXT
);
CREATE TABLE anitya_link (
package TEXT PRIMARY KEY,
projectid INTEGER
);
CREATE TABLE anitya_projects (
id INTEGER PRIMARY KEY,
name TEXT,
homepage TEXT,
ecosystem TEXT,
backend TEXT,
version_url TEXT,
regex TEXT,
latest_version TEXT,
updated_on INTEGER,
created_on INTEGER
);
CREATE INDEX idx_anitya_link ON anitya_link (projectid);
CREATE INDEX idx_anitya_projects ON anitya_projects (name);

CREATE VIEW v_package_upstream AS
SELECT
  package, coalesce(pu.version, ap.latest_version) "version",
  coalesce(pu.time, ap.updated_on) updated,
  coalesce(pu.url, ('https://release-monitoring.org/project/' || ap.id || '/')) url,
  pu.tarball
FROM (
  SELECT package FROM package_upstream
  UNION
  SELECT package FROM anitya_link
) p
LEFT JOIN package_upstream pu USING (package)
LEFT JOIN anitya_link al USING (package)
LEFT JOIN anitya_projects ap ON al.projectid=ap.id;

-- *-marks.db

CREATE TABLE marks (
tree SMALLINT,
name TEXT,
rid INTEGER,
uuid TEXT,
githash TEXT,
PRIMARY KEY (tree, name)
);
CREATE TABLE committers (
email TEXT PRIMARY KEY,
name TEXT
);
CREATE TABLE package_rel (
tree SMALLINT,
rid INTEGER,
package TEXT,
version TEXT,
release TEXT,
epoch TEXT,
message TEXT,
PRIMARY KEY (tree, rid, package)
);
CREATE TABLE branches (
tree SMALLINT,
rid INTEGER,
tagid INTEGER,
tagname TEXT,
PRIMARY KEY (tree, rid, tagid)
);
CREATE INDEX idx_marks ON marks (rid, tree);
CREATE INDEX idx_package_rel ON package_rel (package);