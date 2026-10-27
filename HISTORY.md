# Reconstructed development history

Early development was not tracked in Git. These commits import real retained local version snapshots, rather than reproducing original Git commits. Import commit messages identify this explicitly.

| Snapshot | Reconstructed author date (+08:00) | Exported files |
|---|---|---|
| retained-2.4 | 2026-06-12T09:45:39+08:00 | 8 |
| retained-3.0 | 2026-06-12T18:05:36+08:00 | 7 |
| retained-3.1 | 2026-06-14T00:12:41+08:00 | 7 |
| retained-3.1-index-fields | 2026-06-14T00:20:45+08:00 | 7 |

Author dates use the newest preserved source-file modification time within each complete snapshot. The source version names and preserved file metadata agree with the June development sequence. Filesystem metadata is limited evidence and is not a complete activity log. The committer date records the actual import time. The current 3.2 release and publication changes use today's actual date.

No commits were added for days without a retained version snapshot. In particular, a file that was modified on an earlier day does not establish a complete snapshot for that day. This history cannot recover every past development session.

The public provenance file records only relative source filenames, hashes and preserved modification timestamps. Original source paths, conversations, note-vault data and local settings are excluded. A generic Windows drive-path example in the oldest schema was replaced with a platform-neutral placeholder before any Git object was created.

See [machine-readable provenance](docs/history-provenance.json).
