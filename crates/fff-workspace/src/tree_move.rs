//! Bounded inventories for directory moves; no recursive copy or deletion.
use crate::journal::{Journal, JournalError, Result, unix_mode};
use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;
use std::fs;
use std::io::Read;
use std::path::Path;

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct TreeEntry {
    pub directory: bool,
    pub hash: Option<String>,
    pub bytes: u64,
    pub unix_mode: Option<u32>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct TreeMove {
    pub from: String,
    pub to: String,
    pub entries: BTreeMap<String, TreeEntry>,
}

fn invalid(message: &str) -> JournalError {
    JournalError::InvalidPath(message.into())
}

pub(crate) fn inventory(journal: &Journal, root: &str) -> Result<BTreeMap<String, TreeEntry>> {
    let root_path = journal.resolve(root)?;
    inventory_path(&root_path)
}

// Caller validates the trusted root and its ancestors. Descendants are checked below.
pub(crate) fn inventory_path(root_path: &Path) -> Result<BTreeMap<String, TreeEntry>> {
    if !fs::symlink_metadata(root_path)?.is_dir() {
        return Err(invalid("directory move requires a directory source"));
    }
    let mut pending = vec![(String::new(), 0usize)];
    let mut entries = BTreeMap::new();
    let mut total = 0u64;
    while let Some((relative, depth)) = pending.pop() {
        if depth > 32 || entries.len() >= 100 || relative.len() > 4096 {
            return Err(invalid(
                "directory inventory exceeds path/count/depth limits",
            ));
        }
        let path = root_path.join(&relative);
        let mut checked = root_path.to_path_buf();
        for component in Path::new(&relative).components() {
            let std::path::Component::Normal(name) = component else {
                return Err(invalid("invalid inventory path"));
            };
            if name == ".ffsake" {
                return Err(invalid("reserved inventory path"));
            }
            checked.push(name);
            if fs::symlink_metadata(&checked)?.file_type().is_symlink() {
                return Err(invalid("inventory symlink"));
            }
        }
        let metadata = fs::symlink_metadata(&path)?;
        let directory = metadata.is_dir();
        let (hash, bytes) = if directory {
            for child in fs::read_dir(&path)? {
                if pending.len() + entries.len() >= 99 {
                    return Err(invalid("directory inventory exceeds 100 entries"));
                }
                let child = child?;
                let name = child
                    .file_name()
                    .into_string()
                    .map_err(|_| invalid("directory names must be UTF-8"))?;
                let name = if relative.is_empty() {
                    name
                } else {
                    format!("{relative}/{name}")
                };
                pending.push((name, depth + 1));
            }
            (None, 0)
        } else if metadata.is_file() {
            let maximum = 4 * 1024 * 1024 - total;
            if metadata.len() > maximum {
                return Err(invalid("directory inventory exceeds 4 MiB"));
            }
            let mut data = Vec::new();
            fs::File::open(&path)?
                .take(maximum + 1)
                .read_to_end(&mut data)?;
            if data.len() as u64 > maximum {
                return Err(invalid("directory inventory exceeds 4 MiB"));
            }
            total += data.len() as u64;
            (
                Some(blake3::hash(&data).to_hex().to_string()),
                data.len() as u64,
            )
        } else {
            return Err(invalid(
                "directory inventory rejects symlinks and special files",
            ));
        };
        entries.insert(
            relative,
            TreeEntry {
                directory,
                hash,
                bytes,
                unix_mode: unix_mode(&path)?,
            },
        );
    }
    Ok(entries)
}

impl TreeMove {
    pub(crate) fn preview(journal: &Journal, from: &str, to: &str) -> Result<Self> {
        let value = Self {
            from: from.into(),
            to: to.into(),
            entries: inventory(journal, from)?,
        };
        value.verify(journal, false)?;
        Ok(value)
    }

    pub(crate) fn verify(&self, journal: &Journal, after: bool) -> Result<()> {
        if self.from.len() > 4096 || self.to.len() > 4096 {
            return Err(invalid("directory move paths exceed 4096 bytes"));
        }
        let from = journal.resolve(&self.from)?;
        let to = journal.resolve(&self.to)?;
        if from.starts_with(&to) || to.starts_with(&from) {
            return Err(invalid("directory move paths must not overlap"));
        }
        let (source, destination) = if after {
            (&self.to, from)
        } else {
            (&self.from, to)
        };
        if destination.try_exists()? || !destination.parent().is_some_and(Path::is_dir) {
            return Err(invalid(
                "directory move destination must be absent with an existing parent",
            ));
        }
        if inventory(journal, source)? != self.entries {
            return Err(JournalError::StateConflict {
                path: source.clone(),
                expected: "original directory inventory".into(),
                actual: "changed directory contents or permissions".into(),
            });
        }
        Ok(())
    }

    pub(crate) fn apply(&self, journal: &Journal, after: bool) -> Result<()> {
        self.verify(journal, !after)?;
        let (from, to) = if after {
            (&self.from, &self.to)
        } else {
            (&self.to, &self.from)
        };
        fs::rename(journal.resolve(from)?, journal.resolve(to)?)?;
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn tree_move_reopened_history_and_stale_inventory() {
        let root = tempfile::tempdir().unwrap();
        fs::create_dir_all(root.path().join("source/empty")).unwrap();
        fs::write(root.path().join("source/data"), b"\0\xff\r\n").unwrap();
        let journal = Journal::open(root.path()).unwrap();
        let movement = TreeMove::preview(&journal, "source", "destination").unwrap();
        assert_eq!(movement.entries.len(), 3);
        let transaction = journal.commit_tree_move(movement).unwrap();
        assert!(!root.path().join("source").exists());
        assert_eq!(
            fs::read(root.path().join("destination/data")).unwrap(),
            b"\0\xff\r\n"
        );
        let journal = Journal::open(root.path()).unwrap();
        fs::write(root.path().join("destination/extra"), b"keep").unwrap();
        assert!(journal.undo(&transaction.id).is_err());
        assert_eq!(
            fs::read(root.path().join("destination/extra")).unwrap(),
            b"keep"
        );
        fs::remove_file(root.path().join("destination/extra")).unwrap();
        journal.undo(&transaction.id).unwrap();
        assert!(root.path().join("source/empty").is_dir());
        journal.redo(&transaction.id).unwrap();
        assert!(root.path().join("destination/empty").is_dir());
    }

    #[test]
    fn tree_preview_rechecks_bytes_and_rejects_unsafe_destinations() {
        let root = tempfile::tempdir().unwrap();
        fs::create_dir(root.path().join("source")).unwrap();
        fs::write(root.path().join("source/data"), b"before").unwrap();
        let journal = Journal::open(root.path()).unwrap();
        for destination in [
            "source",
            "./source",
            "source/child",
            ".",
            "../escape",
            ".ffsake/tree",
            "missing/tree",
        ] {
            assert!(
                TreeMove::preview(&journal, "source", destination).is_err(),
                "{destination}"
            );
        }
        let movement = TreeMove::preview(&journal, "source", "destination").unwrap();
        fs::write(root.path().join("source/data"), b"after").unwrap();
        assert!(journal.commit_tree_move(movement).is_err());
        assert!(!root.path().join("destination").exists());
        assert_eq!(fs::read(root.path().join("source/data")).unwrap(), b"after");
        fs::create_dir(root.path().join("source/.ffsake")).unwrap();
        assert!(TreeMove::preview(&journal, "source", "destination").is_err());
    }

    #[test]
    fn inventory_limits_reject_before_move() {
        let root = tempfile::tempdir().unwrap();
        fs::create_dir(root.path().join("source")).unwrap();
        let journal = Journal::open(root.path()).unwrap();
        for index in 0..99 {
            fs::write(root.path().join(format!("source/{index}")), b"").unwrap();
        }
        assert_eq!(
            TreeMove::preview(&journal, "source", "destination")
                .unwrap()
                .entries
                .len(),
            100
        );
        fs::write(root.path().join("source/extra"), b"").unwrap();
        assert!(TreeMove::preview(&journal, "source", "destination").is_err());
        fs::remove_file(root.path().join("source/extra")).unwrap();
        fs::write(root.path().join("source/0"), vec![0; 4 * 1024 * 1024 + 1]).unwrap();
        assert!(TreeMove::preview(&journal, "source", "destination").is_err());
        assert!(!root.path().join("destination").exists());
    }

    #[test]
    fn deep_trees_and_late_destination_collisions_reject() {
        let root = tempfile::tempdir().unwrap();
        fs::create_dir(root.path().join("source")).unwrap();
        let journal = Journal::open(root.path()).unwrap();
        let movement = TreeMove::preview(&journal, "source", "destination").unwrap();
        fs::create_dir(root.path().join("destination")).unwrap();
        fs::write(root.path().join("destination/keep"), b"keep").unwrap();
        assert!(journal.commit_tree_move(movement).is_err());
        assert_eq!(
            fs::read(root.path().join("destination/keep")).unwrap(),
            b"keep"
        );
        let mut nested = root.path().join("source");
        for _ in 0..33 {
            nested.push("nested");
            fs::create_dir(&nested).unwrap();
        }
        assert!(TreeMove::preview(&journal, "source", "other").is_err());
        assert!(root.path().join("source").is_dir());
    }

    #[cfg(unix)]
    #[test]
    fn tree_modes_and_symlinks_are_guarded() {
        use std::os::unix::fs::{PermissionsExt, symlink};
        let root = tempfile::tempdir().unwrap();
        fs::create_dir(root.path().join("source")).unwrap();
        fs::write(root.path().join("source/run"), b"script").unwrap();
        fs::set_permissions(
            root.path().join("source/run"),
            fs::Permissions::from_mode(0o751),
        )
        .unwrap();
        let journal = Journal::open(root.path()).unwrap();
        let movement = TreeMove::preview(&journal, "source", "destination").unwrap();
        fs::set_permissions(
            root.path().join("source/run"),
            fs::Permissions::from_mode(0o700),
        )
        .unwrap();
        assert!(journal.commit_tree_move(movement).is_err());
        symlink(
            root.path().join("source/run"),
            root.path().join("source/link"),
        )
        .unwrap();
        assert!(TreeMove::preview(&journal, "source", "destination").is_err());
        fs::remove_file(root.path().join("source/link")).unwrap();
        let socket =
            std::os::unix::net::UnixListener::bind(root.path().join("source/socket")).unwrap();
        assert!(TreeMove::preview(&journal, "source", "destination").is_err());
        drop(socket);
        fs::remove_file(root.path().join("source/socket")).unwrap();
        let movement = TreeMove::preview(&journal, "source", "destination").unwrap();
        let transaction = journal.commit_tree_move(movement).unwrap();
        assert_eq!(
            unix_mode(&root.path().join("destination/run")).unwrap(),
            Some(0o700)
        );
        journal.undo(&transaction.id).unwrap();
        assert_eq!(
            unix_mode(&root.path().join("source/run")).unwrap(),
            Some(0o700)
        );
    }
}
