//! Private child journals for one independently previewed mixed transaction.
use super::*;
use std::collections::BTreeSet;

impl Journal {
    fn member_journal(&self, id: &str) -> Result<Journal> {
        validate_transaction_id(id)?;
        let root = self.journal_root.join(id).join("members");
        self.check_internal_path(&root)?;
        Ok(Journal {
            workspace_root: self.workspace_root.clone(),
            journal_root: root,
        })
    }

    pub(crate) fn commit_group<F>(&self, count: usize, mut commit: F) -> Result<TransactionManifest>
    where
        F: FnMut(&Journal, usize) -> Result<TransactionManifest>,
    {
        if !(2..=100).contains(&count) {
            return Err(JournalError::Operation(
                "mixed batch requires 2..=100 members".into(),
            ));
        }
        let mut manifest = TransactionManifest {
            id: self.reserve_transaction()?,
            state: TransactionState::Prepared,
            label: Some("mixed file/directory batch".into()),
            files: vec![],
            created_directories: vec![],
            directory: None,
            tree_move: None,
            tree_archive: None,
            members: Some(vec![]),
        };
        self.write_manifest(&manifest)?;
        let nested = self.member_journal(&manifest.id)?;
        fs::create_dir(&nested.journal_root)?;
        let mut completed = Vec::new();
        for index in 0..count {
            let child = match commit(&nested, index) {
                Ok(child) => child,
                Err(error) => {
                    return Err(self.recover_members(
                        &nested,
                        &manifest.id,
                        &completed,
                        false,
                        error,
                    ));
                }
            };
            completed.push(child.id.clone());
            manifest.members.as_mut().unwrap().push(child.id);
            if let Err(error) = self.write_manifest(&manifest) {
                return Err(self.recover_members(&nested, &manifest.id, &completed, false, error));
            }
        }
        manifest.state = TransactionState::Applied;
        if let Err(error) = self.write_manifest(&manifest) {
            return Err(self.recover_members(&nested, &manifest.id, &completed, false, error));
        }
        Ok(manifest)
    }

    fn group_members<'a>(
        &self,
        manifest: &'a TransactionManifest,
    ) -> Result<(&'a [String], Journal)> {
        let members = manifest
            .members
            .as_deref()
            .ok_or_else(|| JournalError::Operation("missing batch members".into()))?;
        if !(2..=100).contains(&members.len())
            || !manifest.files.is_empty()
            || !manifest.created_directories.is_empty()
            || manifest.directory.is_some()
            || manifest.tree_move.is_some()
            || manifest.tree_archive.is_some()
        {
            return Err(JournalError::Operation(
                "invalid mixed batch manifest".into(),
            ));
        }
        let mut seen = BTreeSet::new();
        for id in members {
            validate_transaction_id(id)?;
            if !seen.insert(id) {
                return Err(JournalError::Operation("duplicate batch member".into()));
            }
        }
        Ok((members, self.member_journal(&manifest.id)?))
    }

    pub(super) fn verify_group(
        &self,
        manifest: &TransactionManifest,
        side: SnapshotSide,
    ) -> Result<()> {
        let (members, nested) = self.group_members(manifest)?;
        let expected = if matches!(side, SnapshotSide::After) {
            TransactionState::Applied
        } else {
            TransactionState::Undone
        };
        for id in members {
            let child = nested.load(id)?;
            if child.members.is_some() || child.state != expected {
                return Err(JournalError::Operation(
                    "batch member has an invalid state or nested batch".into(),
                ));
            }
            nested.verify_side(&child, side)?;
            nested.verify_snapshots(&child)?;
        }
        Ok(())
    }

    pub(super) fn transition_group(
        &self,
        mut manifest: TransactionManifest,
        side: SnapshotSide,
        state: TransactionState,
    ) -> Result<TransactionManifest> {
        self.verify_group(&manifest, side.opposite())?;
        let (members, nested) = self.group_members(&manifest)?;
        let after = matches!(side, SnapshotSide::After);
        let order = if after {
            members.to_vec()
        } else {
            members.iter().rev().cloned().collect()
        };
        let mut completed = Vec::new();
        for id in order {
            let result = if after {
                nested.redo(&id)
            } else {
                nested.undo(&id)
            };
            if let Err(error) = result {
                return Err(self.recover_members(&nested, &manifest.id, &completed, !after, error));
            }
            completed.push(id);
        }
        manifest.state = state;
        if let Err(error) = self.write_manifest(&manifest) {
            return Err(self.recover_members(&nested, &manifest.id, &completed, !after, error));
        }
        Ok(manifest)
    }

    fn recover_members(
        &self,
        nested: &Journal,
        parent: &str,
        completed: &[String],
        after: bool,
        original: JournalError,
    ) -> JournalError {
        let failures = completed
            .iter()
            .rev()
            .filter_map(|id| {
                let result = if after {
                    nested.redo(id)
                } else {
                    nested.undo(id)
                };
                result.err().map(|error| format!("{id}: {error}"))
            })
            .collect::<Vec<_>>();
        if failures.is_empty() {
            original
        } else {
            JournalError::RollbackFailed {
                original: format!("batch {parent}: {original}"),
                failures,
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn create(journal: &Journal, path: &str) -> Result<TransactionManifest> {
        journal.commit_changes(
            None,
            &[JournalChange {
                path: path.into(),
                before: None,
                after: Some(path.as_bytes().to_vec()),
            }],
        )
    }

    #[test]
    #[cfg(unix)]
    fn symlinked_member_storage_is_rejected() {
        use std::os::unix::fs::symlink;
        let root = tempfile::tempdir().unwrap();
        let journal = Journal::open(root.path()).unwrap();
        let manifest = journal
            .commit_group(2, |nested, index| create(nested, &format!("file{index}")))
            .unwrap();
        let nested = journal.member_journal(&manifest.id).unwrap();
        let saved = nested.journal_root.with_file_name("saved-members");
        fs::rename(&nested.journal_root, &saved).unwrap();
        symlink(&saved, &nested.journal_root).unwrap();
        assert!(journal.undo(&manifest.id).is_err());
        assert_eq!(fs::read(root.path().join("file0")).unwrap(), b"file0");
        assert_eq!(fs::read(root.path().join("file1")).unwrap(), b"file1");
    }

    #[test]
    fn failed_group_commit_rolls_back_prior_members() {
        let root = tempfile::tempdir().unwrap();
        let journal = Journal::open(root.path()).unwrap();
        let error = journal
            .commit_group(2, |nested, index| {
                if index == 0 {
                    create(nested, "first")
                } else {
                    Err(JournalError::Operation("injected failure".into()))
                }
            })
            .unwrap_err();
        assert!(error.to_string().contains("injected failure"));
        assert!(!root.path().join("first").exists());
    }

    #[test]
    fn group_persistence_failure_restores_children_and_allows_retry() {
        let root = tempfile::tempdir().unwrap();
        let journal = Journal::open(root.path()).unwrap();
        let manifest = journal
            .commit_group(2, |nested, index| create(nested, &format!("file{index}")))
            .unwrap();
        let collision = journal.journal_root.join(&manifest.id).join(format!(
            ".manifest.json.ffsake-journal-tmp-{}",
            std::process::id()
        ));
        fs::write(&collision, b"preserve").unwrap();
        assert!(journal.undo(&manifest.id).is_err());
        assert_eq!(fs::read(root.path().join("file0")).unwrap(), b"file0");
        assert_eq!(fs::read(root.path().join("file1")).unwrap(), b"file1");
        assert_eq!(
            journal.load(&manifest.id).unwrap().state,
            TransactionState::Applied
        );
        fs::remove_file(&collision).unwrap();
        journal.undo(&manifest.id).unwrap();
        fs::write(&collision, b"preserve").unwrap();
        assert!(journal.redo(&manifest.id).is_err());
        assert!(!root.path().join("file0").exists());
        assert!(!root.path().join("file1").exists());
        assert_eq!(
            journal.load(&manifest.id).unwrap().state,
            TransactionState::Undone
        );
        fs::remove_file(collision).unwrap();
        journal.redo(&manifest.id).unwrap();
    }

    #[test]
    fn group_rejects_corrupted_snapshot_and_duplicate_members_before_writes() {
        let root = tempfile::tempdir().unwrap();
        let journal = Journal::open(root.path()).unwrap();
        let mut manifest = journal
            .commit_group(2, |nested, index| create(nested, &format!("file{index}")))
            .unwrap();
        let members = manifest.members.clone().unwrap();
        manifest.members = Some(vec![members[0].clone(), members[0].clone()]);
        journal.write_manifest(&manifest).unwrap();
        assert!(journal.undo(&manifest.id).is_err());
        manifest.members = Some(members.clone());
        journal.write_manifest(&manifest).unwrap();
        let nested = journal.member_journal(&manifest.id).unwrap();
        let child = nested.load(&members[0]).unwrap();
        let snapshot = nested
            .journal_root
            .join(&child.id)
            .join("after")
            .join(&child.files[0].snapshot_key);
        fs::write(snapshot, b"corrupt").unwrap();
        assert!(journal.undo(&manifest.id).is_err());
        assert_eq!(fs::read(root.path().join("file0")).unwrap(), b"file0");
        assert_eq!(fs::read(root.path().join("file1")).unwrap(), b"file1");
    }
}
