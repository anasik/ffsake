//! Directory lifecycle for parents created by file transactions.
use crate::journal::{Journal, JournalChange, JournalError, Result, TransactionFile, unix_mode};
use serde::{Deserialize, Serialize};
use std::collections::{BTreeMap, BTreeSet};
use std::fs;
use std::io;
use std::path::{Path, PathBuf};

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct CreatedDirectory {
    pub path: String,
    pub unix_mode: Option<u32>,
}

fn conflict(path: &str, expected: &str, actual: &str) -> JournalError {
    JournalError::StateConflict {
        path: path.into(),
        expected: expected.into(),
        actual: actual.into(),
    }
}

pub(crate) fn collect(
    journal: &Journal,
    root: &Path,
    changes: &[JournalChange],
) -> Result<Vec<CreatedDirectory>> {
    let mut directories = BTreeMap::new();
    for change in changes.iter().filter(|change| change.after.is_some()) {
        let resolved = journal.resolve(&change.path)?;
        let mut parent = resolved.parent();
        while let Some(path) = parent.filter(|path| *path != root) {
            match fs::symlink_metadata(path) {
                Ok(metadata) if metadata.is_dir() => break,
                Ok(_) => {
                    return Err(conflict(
                        &change.path,
                        "directory parent",
                        "non-directory parent",
                    ));
                }
                Err(error) if error.kind() == io::ErrorKind::NotFound => {
                    let relative = path
                        .strip_prefix(root)
                        .map_err(|_| {
                            conflict(&change.path, "workspace parent", "outside workspace")
                        })?
                        .to_str()
                        .ok_or_else(|| conflict(&change.path, "UTF-8 parent", "non-UTF-8 parent"))?
                        .to_string();
                    directories.insert(
                        relative.clone(),
                        CreatedDirectory {
                            path: relative,
                            unix_mode: None,
                        },
                    );
                    if directories.len() > 1000 {
                        return Err(conflict(
                            &change.path,
                            "at most 1000 created parents",
                            "too many parents",
                        ));
                    }
                }
                Err(error) => return Err(error.into()),
            }
            parent = path.parent();
        }
    }
    let mut values = directories.into_values().collect::<Vec<_>>();
    values.sort_by_key(|entry| Path::new(&entry.path).components().count());
    Ok(values)
}

fn paths(journal: &Journal, directories: &[CreatedDirectory]) -> Result<Vec<PathBuf>> {
    if directories.len() > 1000 {
        return Err(conflict(
            "parents",
            "at most 1000 entries",
            "too many entries",
        ));
    }
    let mut seen = BTreeSet::new();
    let mut previous_depth = 0;
    directories
        .iter()
        .map(|entry| {
            let path = journal.resolve(&entry.path)?;
            let depth = path.components().count();
            if !seen.insert(path.clone())
                || depth < previous_depth
                || entry.unix_mode.is_some_and(|mode| mode > 0o777)
            {
                return Err(conflict(
                    &entry.path,
                    "unique parent-first directory entries",
                    "invalid parent metadata",
                ));
            }
            previous_depth = depth;
            Ok(path)
        })
        .collect()
}

pub(crate) fn verify(
    journal: &Journal,
    directories: &[CreatedDirectory],
    files: &[TransactionFile],
    after: bool,
) -> Result<()> {
    let resolved = paths(journal, directories)?;
    let mut allowed = resolved.iter().cloned().collect::<BTreeSet<_>>();
    for file in files.iter().filter(|file| file.after_hash.is_some()) {
        allowed.insert(journal.resolve(&file.path)?);
    }
    let new_files = files
        .iter()
        .filter(|file| file.before_hash.is_none() && file.after_hash.is_some())
        .map(|file| journal.resolve(&file.path))
        .collect::<Result<Vec<_>>>()?;
    for (entry, path) in directories.iter().zip(resolved) {
        if !new_files
            .iter()
            .any(|file| file != &path && file.starts_with(&path))
        {
            return Err(conflict(
                &entry.path,
                "ancestor of a transaction-created file",
                "unrelated parent metadata",
            ));
        }
        if !after {
            if path.try_exists()? {
                return Err(conflict(
                    &entry.path,
                    "absent created parent",
                    "existing path",
                ));
            }
            continue;
        }
        if !fs::symlink_metadata(&path)?.is_dir() || unix_mode(&path)? != entry.unix_mode {
            return Err(conflict(
                &entry.path,
                "original directory and permissions",
                "changed parent",
            ));
        }
        for child in fs::read_dir(&path)? {
            if !allowed.contains(&child?.path()) {
                return Err(conflict(
                    &entry.path,
                    "only transaction-owned children",
                    "unexpected directory content",
                ));
            }
        }
    }
    Ok(())
}

pub(crate) fn create(
    journal: &Journal,
    directories: &mut [CreatedDirectory],
    recovery: bool,
) -> Result<()> {
    let resolved = paths(journal, directories)?;
    let mut owned = Vec::new();
    for (entry, path) in directories.iter_mut().zip(resolved) {
        if recovery && path.try_exists()? {
            if !path.is_dir() || unix_mode(&path)? != entry.unix_mode {
                return Err(conflict(
                    &entry.path,
                    "original parent directory",
                    "changed recovery parent",
                ));
            }
            continue;
        }
        let operation = (|| -> Result<()> {
            fs::create_dir(&path)?;
            owned.push(path.clone());
            #[cfg(unix)]
            if let Some(mode) = entry.unix_mode {
                use std::os::unix::fs::PermissionsExt;
                fs::set_permissions(&path, fs::Permissions::from_mode(mode))?;
            }
            entry.unix_mode = unix_mode(&path)?;
            Ok(())
        })();
        if let Err(error) = operation {
            let failures = owned
                .iter()
                .rev()
                .filter_map(|path| {
                    fs::remove_dir(path)
                        .err()
                        .map(|error| format!("{}: {error}", path.display()))
                })
                .collect::<Vec<_>>();
            return if failures.is_empty() {
                Err(error)
            } else {
                Err(JournalError::RollbackFailed {
                    original: error.to_string(),
                    failures,
                })
            };
        }
    }
    Ok(())
}

pub(crate) fn remove(journal: &Journal, directories: &[CreatedDirectory]) -> Result<()> {
    let resolved = paths(journal, directories)?;
    for (entry, path) in directories.iter().zip(resolved).rev() {
        if unix_mode(&path)? != entry.unix_mode {
            return Err(conflict(
                &entry.path,
                "original parent permissions",
                "changed parent permissions",
            ));
        }
        fs::remove_dir(path)?;
    }
    Ok(())
}
