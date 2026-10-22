//! One workspace, one resident search index. MatchSet handles retain server-owned ranges and hashes.
use crate::file_ops::{FileOperation, FilePlan};
use crate::journal::Journal;
use crate::search::{FffSearchIndex, MatchSet, SearchMode};
use crate::session::EditorSession;
use crate::transform::{self, Mutation, Transform};
use crate::{FileSelector, PathSelector, ReplaceMode, Workspace};
use serde::Deserialize;
use serde_json::{Value, json};
use std::collections::BTreeMap;

type Result<T> = std::result::Result<T, Box<dyn std::error::Error>>;
const MAX_HANDLES: usize = 128;

#[derive(Debug, Deserialize)]
#[serde(tag = "op", rename_all = "snake_case", deny_unknown_fields)]
pub enum Request {
    Read {
        path: String,
        start: Option<usize>,
        end: Option<usize>,
    },
    List {
        path: String,
    },
    Target {
        target: FileSelector,
    },
    Search {
        pattern: String,
        #[serde(default)]
        regex: bool,
        target: Option<FileSelector>,
        #[serde(default = "default_limit")]
        limit: usize,
        #[serde(default)]
        offset: usize,
        #[serde(default)]
        details: bool,
    },
    PreviewMatches {
        handle: u64,
        replacement: String,
        expected_matches: usize,
    },
    PreviewEdit {
        path: String,
        old_text: String,
        new_text: String,
        expected_matches: usize,
    },
    PreviewCreate {
        path: String,
        content: String,
    },
    PreviewCopy {
        from: String,
        to: String,
    },
    PreviewMove {
        from: String,
        to: String,
    },
    PreviewDelete {
        path: String,
    },
    Preview {
        transform: Transform,
    },
    Apply {
        handle: u64,
    },
    Undo {
        transaction: String,
    },
    Redo {
        transaction: String,
    },
    Release {
        handle: u64,
    },
    Refresh {},
}

fn default_limit() -> usize {
    100
}

pub struct Resident {
    session: EditorSession,
    index: FffSearchIndex,
    matches: BTreeMap<u64, MatchSet>,
    plans: BTreeMap<u64, Transform>,
    file_plans: BTreeMap<u64, FilePlan>,
    next: u64,
    index_dirty: bool,
}

impl Resident {
    pub fn open(workspace: Workspace) -> Result<Self> {
        let index = FffSearchIndex::open(&workspace)?;
        Ok(Self {
            session: EditorSession::new(workspace),
            index,
            matches: BTreeMap::new(),
            plans: BTreeMap::new(),
            file_plans: BTreeMap::new(),
            next: 1,
            index_dirty: false,
        })
    }

    fn allocate(&mut self) -> Result<u64> {
        if self.matches.len() + self.plans.len() + self.file_plans.len() >= MAX_HANDLES {
            return Err("release unused handles (limit 128)".into());
        }
        let handle = self.next;
        self.next = self.next.checked_add(1).ok_or("handle space exhausted")?;
        Ok(handle)
    }

    fn preview(&mut self, mut plan: Transform) -> Result<Value> {
        let workspace = self.session.workspace();
        // Freeze selector membership and hashes before preview, including generic transforms.
        let files = workspace.select_files(&plan.target)?;
        let mut hashes = BTreeMap::new();
        for path in files {
            let path = workspace.relative_display(&path);
            hashes.insert(path.clone(), workspace.content_hash(&path)?);
        }
        plan.target = FileSelector {
            include: hashes.keys().cloned().map(PathSelector::Exact).collect(),
            exclude: vec![],
        };
        plan.operations
            .insert(0, Mutation::AssertHashes { expected: hashes });
        let preview = crate::preview::preview(workspace, &plan)?;
        let handle = self.allocate()?;
        self.plans.insert(handle, plan);
        Ok(json!({"handle": handle, "preview": preview}))
    }

    fn preview_file(&mut self, operation: FileOperation) -> Result<Value> {
        let plan = FilePlan::preview(self.session.workspace(), operation)?;
        let preview = plan.description();
        let handle = self.allocate()?;
        self.file_plans.insert(handle, plan);
        Ok(json!({"handle": handle, "preview": preview}))
    }

    fn history(&mut self, transaction: &str, redo: bool) -> Result<Value> {
        // Journal::load joins the supplied id: forbid path syntax at this boundary.
        if transaction.is_empty()
            || !transaction
                .bytes()
                .all(|b| b.is_ascii_alphanumeric() || b == b'-' || b == b'_')
        {
            return Err("invalid transaction id".into());
        }
        let journal = Journal::open(self.session.workspace().root())?;
        let result = if redo {
            journal.redo(transaction)?
        } else {
            journal.undo(transaction)?
        };
        self.index_dirty = true;
        Ok(serde_json::to_value(result)?)
    }

    pub fn execute(&mut self, request: Request) -> Result<Value> {
        match request {
            Request::Read { path, start, end } => Ok(serde_json::to_value(
                self.session.workspace().read_text(path, start, end)?,
            )?),
            Request::List { path } => Ok(serde_json::to_value(
                self.session.workspace().list_dir(path)?,
            )?),
            Request::Target { target } => {
                Ok(json!({"files": self.session.set_target(target)?.files}))
            }
            Request::Search {
                pattern,
                regex,
                target,
                limit,
                offset,
                details,
            } => {
                if limit == 0 || limit > 1000 {
                    return Err("search limit must be 1..=1000".into());
                }
                if self.index_dirty {
                    self.index.refresh()?;
                    self.index_dirty = false;
                }
                let target = target.or_else(|| {
                    self.session.active_target().map(|a| FileSelector {
                        include: a.files.iter().cloned().map(PathSelector::Exact).collect(),
                        exclude: vec![],
                    })
                });
                let set = self.index.search(
                    self.session.workspace(),
                    &pattern,
                    if regex {
                        SearchMode::Regex
                    } else {
                        SearchMode::Literal
                    },
                    target.as_ref(),
                    limit,
                    offset,
                )?;
                let handle = self.allocate()?;
                let mut response = json!({"handle": handle, "matches": set.ranges.len(), "files": set.file_hashes.len(), "next_file_offset": set.next_file_offset});
                if details {
                    response["ranges"] = serde_json::to_value(&set.ranges)?;
                }
                self.matches.insert(handle, set);
                Ok(response)
            }
            Request::PreviewMatches {
                handle,
                replacement,
                expected_matches,
            } => {
                let set = self.matches.get(&handle).ok_or("unknown MatchSet handle")?;
                if set.ranges.len() != expected_matches {
                    return Err("unexpected match count".into());
                }
                if set.ranges.is_empty() {
                    return Err("empty MatchSet".into());
                }
                let plan = set.replace_transform(self.session.workspace(), replacement)?;
                self.preview(plan)
            }
            Request::PreviewEdit {
                path,
                old_text,
                new_text,
                expected_matches,
            } => {
                if expected_matches == 0 {
                    return Err("expected_matches must be positive".into());
                }
                // Exact selectors also accept directories; this operation is
                // deliberately limited to one existing, safe UTF-8 file.
                self.session.workspace().read_text(&path, None, None)?;
                self.preview(Transform {
                    target: FileSelector::exact(path),
                    operations: vec![Mutation::Replace {
                        mode: ReplaceMode::Literal,
                        pattern: old_text,
                        replacement: new_text,
                        expected_matches: Some(expected_matches),
                    }],
                    max_files: Some(1),
                })
            }
            Request::PreviewCreate { path, content } => {
                self.preview_file(FileOperation::Create { path, content })
            }
            Request::PreviewCopy { from, to } => {
                self.preview_file(FileOperation::Copy { from, to })
            }
            Request::PreviewMove { from, to } => {
                self.preview_file(FileOperation::Move { from, to })
            }
            Request::PreviewDelete { path } => {
                self.preview_file(FileOperation::Delete { path })
            }
            Request::Preview { transform } => self.preview(transform),
            Request::Apply { handle } => {
                if let Some(plan) = self.file_plans.get(&handle) {
                    let result = plan.apply(self.session.workspace())?;
                    self.file_plans.remove(&handle);
                    self.index_dirty = true;
                    return Ok(result);
                }
                let plan = self.plans.get(&handle).ok_or("unknown preview handle")?;
                let result = transform::execute(
                    self.session.workspace(),
                    plan,
                    Some("resident transform".into()),
                )?;
                self.plans.remove(&handle);
                self.index_dirty = true;
                Ok(
                    json!({"transaction": result.transaction.map(|t| t.id), "changed_files": result.changed_files}),
                )
            }
            Request::Undo { transaction } => self.history(&transaction, false),
            Request::Redo { transaction } => self.history(&transaction, true),
            Request::Release { handle } => {
                let removed = self.matches.remove(&handle).is_some()
                    | self.plans.remove(&handle).is_some()
                    | self.file_plans.remove(&handle).is_some();
                Ok(json!({"released": removed}))
            }
            Request::Refresh {} => {
                self.index.refresh()?;
                self.index_dirty = false;
                Ok(json!({"refreshed": true}))
            }
        }
    }
}
