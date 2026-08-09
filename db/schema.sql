-- Coderift Technologies Database Schema
-- SQLite Database

CREATE TABLE engineers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('junior', 'senior', 'lead')),
    email TEXT UNIQUE NOT NULL,
    access_code TEXT UNIQUE NOT NULL,
    active BOOLEAN NOT NULL DEFAULT 1
);

CREATE TABLE repositories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL,
    description TEXT,
    owner_team TEXT NOT NULL
);

CREATE TABLE pull_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    repository_id INTEGER NOT NULL,
    title TEXT NOT NULL,
    description TEXT,
    author_id INTEGER NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('Open', 'Approved', 'Merged', 'Rejected')),
    reviewer_id INTEGER,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (repository_id) REFERENCES repositories(id),
    FOREIGN KEY (author_id) REFERENCES engineers(id),
    FOREIGN KEY (reviewer_id) REFERENCES engineers(id)
);

CREATE TABLE environments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL CHECK(name IN ('staging', 'production')),
    repository_id INTEGER NOT NULL,

    FOREIGN KEY (repository_id) REFERENCES repositories(id),
    UNIQUE (repository_id, name)
);

CREATE TABLE deployments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    repository_id INTEGER NOT NULL,
    environment_id INTEGER NOT NULL,
    deployed_by INTEGER NOT NULL,
    pull_request_id INTEGER NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('Pending', 'InProgress', 'Succeeded', 'Failed', 'RolledBack')),
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    notes TEXT,

    FOREIGN KEY (repository_id) REFERENCES repositories(id),
    FOREIGN KEY (environment_id) REFERENCES environments(id),
    FOREIGN KEY (deployed_by) REFERENCES engineers(id),
    FOREIGN KEY (pull_request_id) REFERENCES pull_requests(id)
);

CREATE TABLE security_scans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pull_request_id INTEGER NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('Passed', 'Failed', 'Pending')),
    scan_type TEXT NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (pull_request_id) REFERENCES pull_requests(id)
);

CREATE TABLE feature_flags (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    repository_id INTEGER NOT NULL,
    environment_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    enabled BOOLEAN NOT NULL DEFAULT 0,

    FOREIGN KEY (repository_id) REFERENCES repositories(id),
    FOREIGN KEY (environment_id) REFERENCES environments(id),
    UNIQUE (repository_id, environment_id, name)
);

CREATE TABLE incidents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    deployment_id INTEGER,
    title TEXT NOT NULL,
    severity TEXT NOT NULL CHECK(severity IN ('low', 'medium', 'high', 'critical')),
    status TEXT NOT NULL CHECK(status IN ('open', 'resolved')),
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    resolved_at DATETIME,

    FOREIGN KEY (deployment_id) REFERENCES deployments(id)
);
