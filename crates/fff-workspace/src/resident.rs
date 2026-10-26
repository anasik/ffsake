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
fn archive_start() -> usize {
    1
}
const MAX_PROPOSAL_CACHE_BYTES: usize = 4 * 1024 * 1024;

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ReadFile {
    path: String,
    start: Option<usize>,
    end: Option<usize>,
}

#[derive(Debug, Deserialize)]
#[serde(tag = "op", rename_all = "snake_case", deny_unknown_fields)]
pub enum Request {
    Git {
        query: crate::workflow::GitQuery,
        #[serde(default)]
        staged: bool,
        #[serde(default)]
        paths: Vec<String>,
    },
    Tasks {},
    RunTask {
        name: String,
    },
    Semantic {
        request: crate::semantic::Request,
    },
    Read {
        path: String,
        start: Option<usize>,
        end: Option<usize>,
    },
    ReadFiles {
        files: Vec<ReadFile>,
    },
    Archive {
        path: String,
        entry: Option<String>,
        #[serde(default)]
        offset: usize,
        #[serde(default = "default_limit")]
        limit: usize,
        #[serde(default = "archive_start")]
        start: usize,
        end: Option<usize>,
        expected_archive_hash: Option<String>,
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
    RustContextFromManifests {
        manifests: Vec<String>,
        files: Vec<String>,
        crate_root: String,
    },
    RustReferences {
        files: Vec<String>,
        crate_root: String,
        path: String,
        position: crate::coordinates::TextPosition,
        edition: String,
        #[serde(default)]
        crates: Vec<crate::lsp::CrateSpec>,
        limit: usize,
    },
    RustInspect {
        files: Vec<String>,
        crate_root: String,
        path: String,
        position: crate::coordinates::TextPosition,
        edition: String,
        #[serde(default)]
        crates: Vec<crate::lsp::CrateSpec>,
        limit: usize,
    },
    RustDefinition {
        files: Vec<String>,
        crate_root: String,
        path: String,
        position: crate::coordinates::TextPosition,
        edition: String,
        #[serde(default)]
        crates: Vec<crate::lsp::CrateSpec>,
        limit: usize,
    },
    RustRenameProposal {
        files: Vec<String>,
        crate_root: String,
        path: String,
        position: crate::coordinates::TextPosition,
        new_name: String,
        edition: String,
        #[serde(default)]
        crates: Vec<crate::lsp::CrateSpec>,
        limit: usize,
    },
    RustCodeActions {
        files: Vec<String>,
        crate_root: String,
        path: String,
        range: crate::coordinates::TextRange,
        edition: String,
        #[serde(default)]
        crates: Vec<crate::lsp::CrateSpec>,
        limit: usize,
    },
    RustHover {
        files: Vec<String>,
        crate_root: String,
        path: String,
        position: crate::coordinates::TextPosition,
        edition: String,
        #[serde(default)]
        crates: Vec<crate::lsp::CrateSpec>,
        max_bytes: usize,
    },
    RustImplementation {
        files: Vec<String>,
        crate_root: String,
        path: String,
        position: crate::coordinates::TextPosition,
        edition: String,
        #[serde(default)]
        crates: Vec<crate::lsp::CrateSpec>,
        limit: usize,
    },
    RustSymbols {
        files: Vec<String>,
        crate_root: String,
        path: String,
        edition: String,
        #[serde(default)]
        crates: Vec<crate::lsp::CrateSpec>,
        limit: usize,
    },
    RustWorkspaceDiagnostics {
        files: Vec<String>,
        crate_root: String,
        paths: Option<Vec<String>>,
        edition: String,
        #[serde(default)]
        crates: Vec<crate::lsp::CrateSpec>,
        limit: usize,
    },
    RustDiagnostics {
        files: Vec<String>,
        crate_root: String,
        path: String,
        edition: String,
        #[serde(default)]
        crates: Vec<crate::lsp::CrateSpec>,
        limit: usize,
    },
    SelectRustIdentifiers {
        paths: Vec<String>,
        identifier: String,
        limit: usize,
    },
    PreviewRustRename {
        rename_set: u64,
        expected_path: String,
        expected_start_byte: usize,
        expected_old_text: String,
        expected_new_name: String,
        expected_edits: usize,
    },
    PreviewRustAction {
        action_set: u64,
        action_index: usize,
        expected_title: String,
        expected_edits: usize,
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
    PreviewRename {
        target: FileSelector,
        from: String,
        to: String,
        expected_matches: usize,
        max_files: usize,
    },
    PreviewFiles {
        operations: Vec<FileOperation>,
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

struct CachedMatches {
    set: MatchSet,
    rust_identifiers: bool,
}

enum ProposalKind {
    Action,
    Rename,
}

struct CachedProposal {
    report: Value,
    bytes: usize,
}

pub struct Resident {
    tasks: crate::workflow::Tasks,
    session: EditorSession,
    index: FffSearchIndex,
    matches: BTreeMap<u64, CachedMatches>,
    actions: BTreeMap<u64, CachedProposal>,
    renames: BTreeMap<u64, CachedProposal>,
    plans: BTreeMap<u64, Transform>,
    file_plans: BTreeMap<u64, FilePlan>,
    next: u64,
    index_dirty: bool,
}

impl Resident {
    pub fn open(workspace: Workspace) -> Result<Self> {
        let index = FffSearchIndex::open(&workspace)?;
        Ok(Self {
            tasks: crate::workflow::Tasks::default(),
            session: EditorSession::new(workspace),
            index,
            matches: BTreeMap::new(),
            actions: BTreeMap::new(),
            renames: BTreeMap::new(),
            plans: BTreeMap::new(),
            file_plans: BTreeMap::new(),
            next: 1,
            index_dirty: false,
        })
    }

    pub fn configure_tasks(&mut self, tasks: crate::workflow::Tasks) {
        self.tasks = tasks;
    }

    fn allocate(&mut self) -> Result<u64> {
        if self.matches.len()
            + self.actions.len()
            + self.renames.len()
            + self.plans.len()
            + self.file_plans.len()
            >= MAX_HANDLES
        {
            return Err("release unused handles (limit 128)".into());
        }
        let handle = self.next;
        self.next = self.next.checked_add(1).ok_or("handle space exhausted")?;
        Ok(handle)
    }

    fn cache_proposal(&mut self, mut report: Value, kind: ProposalKind) -> Result<Value> {
        let bytes = serde_json::to_vec(&report)?.len();
        let cached_bytes: usize = self
            .actions
            .values()
            .chain(self.renames.values())
            .map(|cached| cached.bytes)
            .sum();
        if cached_bytes + bytes > MAX_PROPOSAL_CACHE_BYTES {
            return Err("release unused action/rename sets (shared 4 MiB cache limit)".into());
        }
        let handle = self.allocate()?;
        let (cache, field) = match kind {
            ProposalKind::Action => (&mut self.actions, "action_set"),
            ProposalKind::Rename => (&mut self.renames, "rename_set"),
        };
        cache.insert(
            handle,
            CachedProposal {
                report: report.clone(),
                bytes,
            },
        );
        report[field] = json!(handle);
        Ok(report)
    }

    fn preview(&mut self, mut plan: Transform) -> Result<Value> {
        let workspace = self.session.workspace();
        // Freeze selector membership and hashes before preview, including generic transforms.
        let files = workspace.select_files(&plan.target)?;
        if let Some(max) = plan.max_files
            && files.len() > max
        {
            return Err(format!(
                "target resolved to {} files, exceeding maximum {max}",
                files.len()
            )
            .into());
        }
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
        workspace.access.transaction(
            plan.target.include.iter().filter_map(|p| {
                if let PathSelector::Exact(p) = p {
                    Some(p.as_str())
                } else {
                    None
                }
            }),
            true,
        )?;
        let preview = crate::preview::preview(workspace, &plan)?;
        let handle = self.allocate()?;
        self.plans.insert(handle, plan);
        Ok(json!({"handle": handle, "preview": preview}))
    }

    fn preview_file(&mut self, operation: FileOperation) -> Result<Value> {
        self.preview_files(vec![operation])
    }

    fn preview_files(&mut self, operations: Vec<FileOperation>) -> Result<Value> {
        let plan = FilePlan::preview_batch(self.session.workspace(), operations)?;
        let retained =
            self.file_plans
                .values()
                .try_fold(plan.retained_bytes()?, |bytes, cached| {
                    Ok::<usize, Box<dyn std::error::Error>>(
                        bytes.saturating_add(cached.retained_bytes()?),
                    )
                })?;
        if retained > crate::file_ops::MAX_FILE_PLAN_BYTES {
            return Err("file preview cache exceeds 16 MiB retained payload budget; apply or release existing handles".into());
        }
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
        let journal = Journal::for_workspace(self.session.workspace())?;
        let result = if redo {
            journal.redo(transaction)?
        } else {
            journal.undo(transaction)?
        };
        self.index_dirty = true;
        Ok(serde_json::to_value(result)?)
    }

    pub fn access(&self) -> crate::access::Access {
        self.session.workspace().access.clone()
    }

    pub fn execute(&mut self, request: Request) -> Result<Value> {
        self.access().refresh()?;
        match request {
            Request::Read { path, start, end } => Ok(serde_json::to_value(
                self.session.workspace().read_text(path, start, end)?,
            )?),
            Request::ReadFiles { files } => {
                if files.is_empty() || files.len() > 32 {
                    return Err("files must contain 1..=32 entries".into());
                }
                for file in &files {
                    self.access()
                        .authorize(std::path::Path::new(&file.path), false)?;
                }
                let mut results = Vec::new();
                let mut remaining = 1024 * 1024;
                for file in files {
                    if file.path.len() > 4096
                        || file.start == Some(0)
                        || file.end == Some(0)
                        || matches!((file.start, file.end), (Some(start), Some(end)) if start > end)
                    {
                        return Err("invalid batch read path or line range".into());
                    }
                    match self.session.workspace().read_text_bounded(
                        std::path::Path::new(&file.path),
                        file.start,
                        file.end,
                        256 * 1024,
                    )? {
                        Some(read) => {
                            if read.text.len() > remaining {
                                return Err("batch read output exceeds 1 MiB text limit".into());
                            }
                            remaining -= read.text.len();
                            results.push(json!({"path":file.path,"status":"ok","read":read}));
                        }
                        None => results.push(json!({"path":file.path,"status":"missing"})),
                    }
                }
                Ok(json!({"files":results}))
            }
            Request::List { path } => Ok(serde_json::to_value(
                self.session.workspace().list_dir(path)?,
            )?),
            Request::Archive {
                path,
                entry,
                offset,
                limit,
                start,
                end,
                expected_archive_hash,
            } => crate::archive::browse(
                self.session.workspace(),
                &path,
                entry.as_deref(),
                offset,
                limit,
                (start, end),
                expected_archive_hash.as_deref(),
            ),
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
                response["coverage"] = serde_json::to_value(&set.coverage)?;
                if details {
                    response["ranges"] = serde_json::to_value(&set.ranges)?;
                }
                self.matches.insert(
                    handle,
                    CachedMatches {
                        set,
                        rust_identifiers: false,
                    },
                );
                Ok(response)
            }
            Request::RustContextFromManifests {
                manifests,
                files,
                crate_root,
            } => crate::manifest_context::import(
                self.session.workspace(),
                &manifests,
                &files,
                &crate_root,
            ),
            Request::RustReferences {
                files,
                crate_root,
                path,
                position,
                edition,
                crates,
                limit,
            } => crate::lsp::rust_references(
                self.session.workspace(),
                &files,
                &crate_root,
                &path,
                position,
                crate::lsp::RustContext {
                    edition: &edition,
                    crates: &crates,
                },
                limit,
            ),
            Request::RustInspect {
                files,
                crate_root,
                path,
                position,
                edition,
                crates,
                limit,
            } => crate::lsp::rust_inspect(
                self.session.workspace(),
                &files,
                &crate_root,
                &path,
                position,
                crate::lsp::RustContext {
                    edition: &edition,
                    crates: &crates,
                },
                limit,
            ),
            Request::RustDefinition {
                files,
                crate_root,
                path,
                position,
                edition,
                crates,
                limit,
            } => crate::lsp::rust_definition(
                self.session.workspace(),
                &files,
                &crate_root,
                &path,
                position,
                crate::lsp::RustContext {
                    edition: &edition,
                    crates: &crates,
                },
                limit,
            ),
            Request::Git {
                query,
                staged,
                paths,
            } => crate::workflow::inspect(self.session.workspace(), query, staged, &paths),
            Request::Tasks {} => Ok(self.tasks.list()),
            Request::RunTask { name } => {
                self.index_dirty = true;
                self.tasks.run(self.session.workspace(), &name)
            }
            Request::Semantic { request } => {
                let report = crate::semantic::query(self.session.workspace(), &request)?;
                if matches!(request.query, crate::semantic::Query::Rename) {
                    self.cache_proposal(report, ProposalKind::Rename)
                } else {
                    Ok(report)
                }
            }
            Request::RustRenameProposal {
                files,
                crate_root,
                path,
                position,
                new_name,
                edition,
                crates,
                limit,
            } => {
                let report = crate::lsp::rust_rename_proposal(
                    self.session.workspace(),
                    &files,
                    &crate_root,
                    &path,
                    crate::lsp::RenameTarget {
                        position,
                        new_name: &new_name,
                    },
                    crate::lsp::RustContext {
                        edition: &edition,
                        crates: &crates,
                    },
                    limit,
                )?;
                self.cache_proposal(report, ProposalKind::Rename)
            }
            Request::RustCodeActions {
                files,
                crate_root,
                path,
                range,
                edition,
                crates,
                limit,
            } => {
                let report = crate::lsp::rust_code_actions(
                    self.session.workspace(),
                    &files,
                    &crate_root,
                    &path,
                    range,
                    crate::lsp::RustContext {
                        edition: &edition,
                        crates: &crates,
                    },
                    limit,
                )?;
                self.cache_proposal(report, ProposalKind::Action)
            }
            Request::PreviewRustRename {
                rename_set,
                expected_path,
                expected_start_byte,
                expected_old_text,
                expected_new_name,
                expected_edits,
            } => {
                let cached = self.renames.get(&rename_set).ok_or("unknown rename set")?;
                let plan = crate::lsp_rename::preview_transform(
                    self.session.workspace(),
                    &cached.report,
                    crate::lsp_rename::Expectation {
                        path: &expected_path,
                        start_byte: expected_start_byte,
                        old_text: &expected_old_text,
                        new_name: &expected_new_name,
                        edits: expected_edits,
                    },
                )?;
                let context = cached.report["analysis_context"].clone();
                let selection = cached.report["prepared"].clone();
                let mut result = self.preview(plan)?;
                result["analysis_context"] = context;
                result["prepared"] = selection;
                result["path"] = json!(expected_path);
                result["new_name"] = json!(expected_new_name);
                result["complete"] = json!(false);
                result["semantic_validated"] = json!(false);
                result["compiler_checked"] = json!(false);
                Ok(result)
            }
            Request::PreviewRustAction {
                action_set,
                action_index,
                expected_title,
                expected_edits,
            } => {
                let cached = self
                    .actions
                    .get(&action_set)
                    .ok_or("unknown code-action set")?;
                let plan = crate::lsp_actions::preview_transform(
                    self.session.workspace(),
                    &cached.report,
                    action_index,
                    &expected_title,
                    expected_edits,
                )?;
                let context = cached.report["analysis_context"].clone();
                let mut result = self.preview(plan)?;
                result["analysis_context"] = context;
                result["action_title"] = json!(expected_title);
                result["semantic_validated"] = json!(false);
                Ok(result)
            }
            Request::RustHover {
                files,
                crate_root,
                path,
                position,
                edition,
                crates,
                max_bytes,
            } => crate::lsp::rust_hover(
                self.session.workspace(),
                &files,
                &crate_root,
                &path,
                position,
                crate::lsp::RustContext {
                    edition: &edition,
                    crates: &crates,
                },
                max_bytes,
            ),
            Request::RustImplementation {
                files,
                crate_root,
                path,
                position,
                edition,
                crates,
                limit,
            } => crate::lsp::rust_implementation(
                self.session.workspace(),
                &files,
                &crate_root,
                &path,
                position,
                crate::lsp::RustContext {
                    edition: &edition,
                    crates: &crates,
                },
                limit,
            ),
            Request::RustSymbols {
                files,
                crate_root,
                path,
                edition,
                crates,
                limit,
            } => crate::lsp::rust_symbols(
                self.session.workspace(),
                &files,
                &crate_root,
                &path,
                crate::lsp::RustContext {
                    edition: &edition,
                    crates: &crates,
                },
                limit,
            ),
            Request::RustWorkspaceDiagnostics {
                files,
                crate_root,
                paths,
                edition,
                crates,
                limit,
            } => crate::lsp::rust_workspace_diagnostics(
                self.session.workspace(),
                &files,
                &crate_root,
                paths.as_deref(),
                crate::lsp::RustContext {
                    edition: &edition,
                    crates: &crates,
                },
                limit,
            ),
            Request::RustDiagnostics {
                files,
                crate_root,
                path,
                edition,
                crates,
                limit,
            } => crate::lsp::rust_diagnostics(
                self.session.workspace(),
                &files,
                &crate_root,
                &path,
                crate::lsp::RustContext {
                    edition: &edition,
                    crates: &crates,
                },
                limit,
            ),
            Request::SelectRustIdentifiers {
                paths,
                identifier,
                limit,
            } => {
                let set = crate::syntax::select_rust_identifiers(
                    self.session.workspace(),
                    &paths,
                    &identifier,
                    limit,
                )?;
                let handle = self.allocate()?;
                let response = json!({"handle":handle,"matches":set.ranges.len(),"files":set.file_hashes.len(),"ranges":set.ranges,"language":"rust","semantic":false});
                self.matches.insert(
                    handle,
                    CachedMatches {
                        set,
                        rust_identifiers: true,
                    },
                );
                Ok(response)
            }
            Request::PreviewMatches {
                handle,
                replacement,
                expected_matches,
            } => {
                let cached = self.matches.get(&handle).ok_or("unknown MatchSet handle")?;
                let set = &cached.set;
                if set.ranges.len() != expected_matches {
                    return Err("unexpected match count".into());
                }
                if set.ranges.is_empty() {
                    return Err("empty MatchSet".into());
                }
                let plan = if cached.rust_identifiers {
                    crate::syntax::rename_transform(
                        self.session.workspace(),
                        set,
                        replacement.as_str(),
                    )?
                } else {
                    set.replace_transform(self.session.workspace(), replacement)?
                };
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
                self.access().authorize(std::path::Path::new(&path), true)?;
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
            Request::PreviewRename {
                target,
                from,
                to,
                expected_matches,
                max_files,
            } => {
                if expected_matches == 0 || !(1..=100).contains(&max_files) {
                    return Err(
                        "expected_matches must be positive and max_files must be 1..=100".into(),
                    );
                }
                self.preview(Transform {
                    target,
                    operations: vec![Mutation::ReplaceIdentifierCasePreserving {
                        from,
                        to,
                        expected_matches: Some(expected_matches),
                    }],
                    max_files: Some(max_files),
                })
            }
            Request::PreviewFiles { operations } => self.preview_files(operations),
            Request::PreviewCreate { path, content } => {
                self.preview_file(FileOperation::Create { path, content })
            }
            Request::PreviewCopy { from, to } => {
                self.preview_file(FileOperation::Copy { from, to })
            }
            Request::PreviewMove { from, to } => {
                self.preview_file(FileOperation::Move { from, to })
            }
            Request::PreviewDelete { path } => self.preview_file(FileOperation::Delete { path }),
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
                    | self.actions.remove(&handle).is_some()
                    | self.renames.remove(&handle).is_some()
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

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn file_preview_budget_releases_capacity_on_release_and_apply() {
        let root = tempfile::tempdir().unwrap();
        std::fs::create_dir(root.path().join("source")).unwrap();
        std::fs::write(
            root.path().join("source/data.bin"),
            vec![0; 3 * 1024 * 1024],
        )
        .unwrap();
        let mut resident = Resident::open(Workspace::new(root.path()).unwrap()).unwrap();
        let mut handles = Vec::new();
        for index in 0..5 {
            let report = resident
                .preview_files(vec![FileOperation::CopyDirectory {
                    from: "source".into(),
                    to: format!("copy-{index}"),
                }])
                .unwrap();
            handles.push(report["handle"].as_u64().unwrap());
        }
        let error = resident
            .preview_files(vec![FileOperation::CopyDirectory {
                from: "source".into(),
                to: "overflow".into(),
            }])
            .unwrap_err();
        assert!(error.to_string().contains("16 MiB"));
        assert_eq!(resident.file_plans.len(), 5);
        assert!(!root.path().join("overflow").exists());
        resident
            .execute(Request::Release { handle: handles[0] })
            .unwrap();
        resident
            .preview_files(vec![FileOperation::CopyDirectory {
                from: "source".into(),
                to: "after-release".into(),
            }])
            .unwrap();
        resident
            .execute(Request::Apply { handle: handles[1] })
            .unwrap();
        assert!(root.path().join("copy-1/data.bin").is_file());
        resident
            .preview_files(vec![FileOperation::CopyDirectory {
                from: "source".into(),
                to: "after-apply".into(),
            }])
            .unwrap();
        assert_eq!(resident.file_plans.len(), 5);
    }

    #[test]
    fn action_and_rename_caches_share_limits_and_release_capacity() {
        let directory = tempfile::tempdir().unwrap();
        let mut resident = Resident::open(Workspace::new(directory.path()).unwrap()).unwrap();
        let large = resident
            .cache_proposal(
                json!({"text":"x".repeat(3 * 1024 * 1024)}),
                ProposalKind::Action,
            )
            .unwrap();
        assert!(
            resident
                .cache_proposal(
                    json!({"text":"y".repeat(2 * 1024 * 1024)}),
                    ProposalKind::Rename
                )
                .is_err()
        );
        resident
            .execute(Request::Release {
                handle: large["action_set"].as_u64().unwrap(),
            })
            .unwrap();
        let renamed = resident
            .cache_proposal(
                json!({"text":"y".repeat(2 * 1024 * 1024)}),
                ProposalKind::Rename,
            )
            .unwrap();
        assert!(
            resident
                .execute(Request::Apply {
                    handle: renamed["rename_set"].as_u64().unwrap()
                })
                .is_err()
        );
        resident
            .execute(Request::Release {
                handle: renamed["rename_set"].as_u64().unwrap(),
            })
            .unwrap();
        let mut last = 0;
        for index in 0..MAX_HANDLES {
            let kind = if index % 2 == 0 {
                ProposalKind::Action
            } else {
                ProposalKind::Rename
            };
            let value = resident.cache_proposal(json!({}), kind).unwrap();
            last = value["action_set"]
                .as_u64()
                .or_else(|| value["rename_set"].as_u64())
                .unwrap();
        }
        assert!(
            resident
                .cache_proposal(json!({}), ProposalKind::Action)
                .is_err()
        );
        assert!(
            resident
                .cache_proposal(json!({}), ProposalKind::Rename)
                .is_err()
        );
        resident.execute(Request::Release { handle: last }).unwrap();
        assert!(
            resident
                .cache_proposal(json!({}), ProposalKind::Rename)
                .is_ok()
        );
    }
}
