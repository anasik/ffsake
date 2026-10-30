//! Reviewed branch, fetch and push operations. Network output is never returned.
use crate::{Workspace, git_write as git};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use std::{collections::BTreeMap, path::Path};
type Result<T> = std::result::Result<T, Box<dyn std::error::Error>>;

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(tag = "action", rename_all = "snake_case", deny_unknown_fields)]
pub enum Operation {
    Create {
        branch: String,
    },
    Switch {
        branch: String,
    },
    Fetch {
        remote: String,
    },
    Push {
        branch: String,
        remote: String,
        remote_branch: String,
        #[serde(default)]
        set_upstream: bool,
    },
}
fn safe_ref(root: &Path, name: &str) -> Result<()> {
    if name.is_empty()
        || name.len() > 256
        || name.starts_with('-')
        || name.starts_with('+')
        || name.bytes().any(|b| b < 32 || b == 127)
    {
        return Err("invalid branch name".into());
    }
    let out = git::run(root, &["check-ref-format", &format!("refs/heads/{name}")])?;
    if !out.status.success() || out.timed_out || out.truncated {
        return Err("invalid branch name".into());
    }
    Ok(())
}
fn safe_remote(remote: &str) -> Result<()> {
    if remote.is_empty()
        || remote.len() > 128
        || remote.starts_with('-')
        || !remote
            .bytes()
            .all(|c| c.is_ascii_alphanumeric() || b"._-".contains(&c))
    {
        return Err("remote must be an explicit configured remote name".into());
    }
    Ok(())
}
fn oid(root: &Path, branch: &str) -> Result<Option<String>> {
    let name = format!("refs/heads/{branch}");
    let o = git::run(root, &["rev-parse", "--verify", "--quiet", &name])?;
    if o.timed_out || o.truncated {
        return Err("cannot inspect branch reference".into());
    }
    if o.status.success() {
        Ok(Some(String::from_utf8(o.stdout)?.trim().to_owned()))
    } else if o.status.code() == Some(1) {
        Ok(None)
    } else {
        Err("cannot inspect branch reference".into())
    }
}
fn config_bool(root: &Path, key: &str) -> Result<bool> {
    let out = git::run(root, &["config", "--bool", "--get", key])?;
    if out.timed_out || out.truncated {
        return Err("cannot verify Git configuration".into());
    }
    match out.status.code() {
        Some(0) => Ok(std::str::from_utf8(&out.stdout)?.trim() == "true"),
        Some(1) => Ok(false),
        _ => Err(format!("unsupported Git setting {key}; use host Git").into()),
    }
}
fn validate_remote(root: &Path, remote: &str, push: bool, tracking: bool) -> Result<()> {
    safe_remote(remote)?;
    if git::config(root, &format!("remote.{remote}.vcs"))?.is_some() {
        return Err("custom remote helpers are unsupported; use host Git".into());
    }
    let mut args = vec!["remote".to_string(), "get-url".into()];
    if push {
        args.push("--push".into());
    }
    args.push("--all".into());
    args.push(remote.into());
    let o = git::command(root, &args)?;
    if !o.status.success() || o.timed_out || o.truncated {
        return Err("remote is missing or its URL cannot be inspected".into());
    }
    let urls = std::str::from_utf8(&o.stdout)?.lines().collect::<Vec<_>>();
    if urls.len() != 1 {
        return Err("remote must have exactly one effective URL for this operation".into());
    }
    let url = urls[0];
    if url.contains("::")
        || url.starts_with('-')
        || url.bytes().any(|c| c < 32)
        || !(url.starts_with('/')
            || url.starts_with("file://")
            || url.starts_with("https://")
            || url.starts_with("http://")
            || url.starts_with("ssh://")
            || (url.contains(':') && !url.contains("://")))
    {
        return Err("unsupported remote transport; use a local, HTTP(S), or SSH remote".into());
    }
    if push {
        for key in [
            format!("remote.{remote}.mirror"),
            "push.followTags".into(),
            "push.gpgSign".into(),
        ] {
            if config_bool(root, &key)? {
                return Err(format!(
                    "Git setting {key} exceeds supported branch-only push; use host Git"
                )
                .into());
            }
        }
        for key in [
            format!("remote.{remote}.receivepack"),
            "push.pushOption".into(),
        ] {
            if git::config(root, &key)?.is_some() {
                return Err(
                    format!("Git setting {key} is unsupported for push; use host Git").into(),
                );
            }
        }
        if let Some(v) = git::config(root, "push.recurseSubmodules")?
            && !["no", "false", "0"].contains(&v.to_ascii_lowercase().as_str())
        {
            return Err("configured recursive submodule push is unsupported; use host Git".into());
        }
    } else {
        for key in [
            "fetch.prune".to_string(),
            "fetch.pruneTags".to_string(),
            format!("remote.{remote}.prune"),
            format!("remote.{remote}.pruneTags"),
        ] {
            if config_bool(root, &key)? {
                return Err(format!(
                    "Git setting {key} exceeds supported branch-only fetch; use host Git"
                )
                .into());
            }
        }
        let upload = format!("remote.{remote}.uploadpack");
        if git::config(root, &upload)?.is_some() {
            return Err(
                format!("Git setting {upload} is unsupported for fetch; use host Git").into(),
            );
        }
        for key in [
            "fetch.recurseSubmodules".to_string(),
            format!("remote.{remote}.fetchRecurseSubmodules"),
        ] {
            if let Some(value) = git::config(root, &key)?
                && !["no", "false", "0"].contains(&value.to_ascii_lowercase().as_str())
            {
                return Err(
                    format!("Git setting {key} enables submodule fetch; use host Git").into(),
                );
            }
        }
        if git::config(root, &format!("remote.{remote}.tagOpt"))?.as_deref() == Some("--tags") {
            return Err("remote is configured to fetch tags; use host Git".into());
        }
    }
    if !tracking {
        return Ok(());
    }
    let fetch = git::run(
        root,
        &["config", "--get-all", &format!("remote.{remote}.fetch")],
    )?;
    if fetch.timed_out || fetch.truncated || !matches!(fetch.status.code(), Some(0 | 1)) {
        return Err("cannot verify remote fetch configuration".into());
    }
    let expected = format!("+refs/heads/*:refs/remotes/{remote}/*");
    let specs = std::str::from_utf8(&fetch.stdout)?
        .lines()
        .collect::<Vec<_>>();
    if specs.len() != 1 || specs[0] != expected {
        return Err("remote must use the standard branch-only fetch mapping; use host Git for custom mappings".into());
    }
    Ok(())
}
fn inspect_switch(workspace: &Workspace, target: &str) -> Result<String> {
    let root = workspace.root();
    let h = git::head(root)?
        .0
        .ok_or("switching an unborn repository is unsupported")?;
    let target_oid = oid(root, target)?.ok_or("local branch does not exist")?;
    let bytes = git::good(git::command(
        root,
        &[
            "diff".into(),
            "--no-ext-diff".into(),
            "--no-textconv".into(),
            "--no-renames".into(),
            "--name-only".into(),
            "-z".into(),
            h.clone(),
            target_oid.clone(),
            "--".into(),
        ],
    )?)?;
    let mut paths = Vec::new();
    let mut contents = blake3::Hasher::new();
    let mut total_bytes = 0u64;
    for raw in bytes.split(|b| *b == 0).filter(|p| !p.is_empty()) {
        if paths.len() >= 1000 {
            return Err("branch switch exceeds the 1000-path review limit".into());
        }
        let name = std::str::from_utf8(raw)?;
        if name
            .split('/')
            .any(|part| part.eq_ignore_ascii_case(".git") || part.eq_ignore_ascii_case(".ffsake"))
        {
            return Err("switch would touch protected .git or .ffsake metadata".into());
        }
        if name == ".gitattributes" || name.ends_with("/.gitattributes") {
            return Err("switch changes attributes; use host Git".into());
        }
        let (path, owner) = workspace.access.authorize(&root.join(name), true)?;
        if !root.starts_with(&owner) || !path.starts_with(root) {
            return Err("switch paths must remain in the selected repository grant".into());
        }
        if let Ok(metadata) = std::fs::symlink_metadata(&path) {
            total_bytes = total_bytes
                .checked_add(metadata.len())
                .ok_or("switch content limit exceeded")?;
            if total_bytes > 64 * 1024 * 1024 {
                return Err("switch review exceeds 64 MiB of worktree content".into());
            }
        }
        contents.update(name.as_bytes());
        contents.update(&[0]);
        contents.update(
            git::hash_file(&path, 32 * 1024 * 1024)?
                .unwrap_or_default()
                .as_bytes(),
        );
        contents.update(&[0]);
        paths.push(name.to_string());
    }
    // Reject symlinks and submodules in either tree before checkout.
    for tree in [&h, &target_oid] {
        let mut args = vec![
            "ls-tree".into(),
            "-r".into(),
            "-z".into(),
            tree.to_string(),
            "--".into(),
        ];
        args.extend(paths.iter().map(|p| format!(":(literal){p}")));
        if paths.is_empty() {
            break;
        }
        let listing = git::good(git::command(root, &args)?)?;
        for line in listing.split(|b| *b == 0).filter(|e| !e.is_empty()) {
            if line.starts_with(b"120000 ") || line.starts_with(b"160000 ") {
                return Err(
                    "switch involving symlinks or submodules is unsupported; use host Git".into(),
                );
            }
        }
    }
    // Smudge/process filters may execute during checkout. Current and index
    // attributes are enough here because switches changing attributes reject.
    for p in &paths {
        for cached in [false, true] {
            let mut args = vec!["check-attr".into(), "-z".into()];
            if cached {
                args.push("--cached".into());
            }
            args.extend(["filter".into(), "--".into(), p.clone()]);
            let a = git::good(git::command(root, &args)?)?;
            contents.update(&a);
            let value =
                std::str::from_utf8(a.split(|b| *b == 0).nth(2).ok_or("invalid attributes")?)?;
            for suffix in ["smudge", "process", "clean"] {
                if git::config(root, &format!("filter.{value}.{suffix}"))?.is_some() {
                    return Err("checkout uses an executable filter; use host Git".into());
                }
            }
        }
    }
    Ok(contents.finalize().to_hex().to_string())
}
#[derive(Clone, PartialEq, Eq)]
struct Snapshot {
    head: (Option<String>, Option<String>),
    index: Option<String>,
    config: String,
    source: Option<String>,
    target: Option<String>,
    worktree: Option<String>,
}
fn snapshot(workspace: &Workspace, op: &Operation) -> Result<Snapshot> {
    let root = workspace.root();
    git::root_check(root)?;
    let head = git::head(root)?;
    let index = git::hash_file(&git::git_path(root, "index")?, 32 * 1024 * 1024)?;
    let cfg = git::run(root, &["config", "--list", "--null"])?;
    if !cfg.status.success() || cfg.timed_out || cfg.truncated {
        return Err("cannot safely inspect Git configuration; details withheld".into());
    }
    let (source, target) = match op {
        Operation::Create { branch } => {
            safe_ref(root, branch)?;
            (None, oid(root, branch)?)
        }
        Operation::Switch { branch } => {
            safe_ref(root, branch)?;
            (None, oid(root, branch)?)
        }
        Operation::Fetch { remote } => {
            validate_remote(root, remote, false, true)?;
            (None, None)
        }
        Operation::Push {
            branch,
            remote,
            remote_branch,
            set_upstream,
        } => {
            safe_ref(root, branch)?;
            safe_ref(root, remote_branch)?;
            validate_remote(root, remote, true, *set_upstream)?;
            (oid(root, branch)?, None)
        }
    };
    Ok(Snapshot {
        head,
        index,
        config: blake3::hash(&cfg.stdout).to_hex().to_string(),
        source,
        target,
        worktree: match op {
            Operation::Switch { branch } if oid(root, branch)?.is_some() => {
                Some(inspect_switch(workspace, branch)?)
            }
            _ => None,
        },
    })
}
fn preflight(workspace: &Workspace, op: &Operation, s: &Snapshot) -> Result<()> {
    let root = workspace.root();
    git::hooks(root).map_err(|_| "Git hook configuration is unsupported; use host Git")?;
    if let Some(value) = git::config(root, "core.fsmonitor")?
        && !["false", "no", "off", "0", ""].contains(&value.to_ascii_lowercase().as_str())
    {
        return Err("configured Git fsmonitor is unsupported; use host Git".into());
    }
    let hook = match op {
        Operation::Switch { .. } => Some("post-checkout"),
        Operation::Push { .. } => Some("pre-push"),
        _ => None,
    };
    if let Some(name) = hook {
        let path = git::git_path(root, "hooks")?.join(name);
        match std::fs::metadata(path) {
            Ok(m) if m.is_file() => {
                #[cfg(unix)]
                {
                    use std::os::unix::fs::PermissionsExt;
                    if m.permissions().mode() & 0o111 != 0 {
                        return Err(format!("executable {name} hook requires host Git").into());
                    }
                }
                #[cfg(not(unix))]
                {
                    return Err(format!("{name} hook requires host Git").into());
                }
            }
            Ok(_) => return Err(format!("unsupported {name} hook path").into()),
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => {}
            Err(e) => return Err(e.into()),
        }
    }
    match op {
        Operation::Create { .. } => {
            if s.head.0.is_none() {
                return Err("creating a branch from unborn HEAD is unsupported".into());
            }
            if s.target.is_some() {
                return Err("branch already exists".into());
            }
        }
        Operation::Switch { branch } => {
            if s.target.is_none() {
                return Err("local branch does not exist".into());
            }
            let _ = branch;
        }
        Operation::Push { .. } => {
            if s.source.is_none() {
                return Err("local source branch does not exist".into());
            }
        }
        Operation::Fetch { .. } => {}
    }
    Ok(())
}
pub(crate) fn branches(workspace: &Workspace) -> Result<Value> {
    let root = workspace.root();
    git::root_check(root)?;
    let h = git::head(root)?;
    let data = git::good(git::run(
        root,
        &[
            "for-each-ref",
            "--format=%(refname)%00%(objectname)%00%(upstream)%00%(upstream:short)",
            "refs/heads/",
        ],
    )?)?;
    let mut items = Vec::new();
    for line in data.split(|b| *b == b'\n').filter(|l| !l.is_empty()) {
        let f = line.split(|b| *b == 0).collect::<Vec<_>>();
        if f.len() != 4 {
            return Err("invalid Git branch output".into());
        }
        let name = std::str::from_utf8(f[0])?
            .strip_prefix("refs/heads/")
            .ok_or("invalid branch ref")?;
        let commit = std::str::from_utf8(f[1])?;
        let upstream_ref = std::str::from_utf8(f[2])?;
        let upstream = std::str::from_utf8(f[3])?;
        items.push(json!({"branch":name,"commit":commit,"upstream":if upstream.is_empty(){None}else{Some(upstream)},"upstream_ref":if upstream_ref.is_empty(){None}else{Some(upstream_ref)},"current":h.1.as_deref()==Some(&format!("refs/heads/{name}"))}));
    }
    Ok(
        json!({"repository":root,"current":h.1.and_then(|r|r.strip_prefix("refs/heads/").map(str::to_owned)),"head":h.0,"branches":items}),
    )
}
struct Review {
    repository: std::path::PathBuf,
    operation: Operation,
    snapshot: Snapshot,
}
struct BranchLock(std::path::PathBuf);
impl Drop for BranchLock {
    fn drop(&mut self) {
        let _ = std::fs::remove_file(&self.0);
    }
}
fn lock_branch(root: &Path, branch: &str) -> Result<BranchLock> {
    let reference = git::git_path(root, &format!("refs/heads/{branch}"))?;
    let mut name = reference.as_os_str().to_os_string();
    name.push(".lock");
    let lock = std::path::PathBuf::from(name);
    let parent = lock.parent().ok_or("invalid branch lock path")?;
    crate::access::no_symlinks(parent)?;
    std::fs::create_dir_all(parent)?;
    crate::access::no_symlinks(parent)?;
    std::fs::OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(&lock)
        .map_err(|_| "cannot lock reviewed branch; another Git operation may be running")?;
    Ok(BranchLock(lock))
}
#[derive(Default)]
pub(crate) struct Reviews {
    next: u64,
    reviews: BTreeMap<u64, Review>,
}
impl Reviews {
    pub(crate) fn review(&mut self, workspace: &Workspace, operation: Operation) -> Result<Value> {
        let s = snapshot(workspace, &operation)?;
        preflight(workspace, &operation, &s)?;
        if snapshot(workspace, &operation)? != s {
            return Err("Git state changed during review; retry".into());
        }
        if self.reviews.len() >= 64 {
            self.reviews.pop_first();
        }
        self.next += 1;
        let id = self.next;
        let result = json!({"review":id,"repository":workspace.root(),"operation":operation,"head":s.head.0,"current_ref":s.head.1,"source_commit":s.source,"target_commit":s.target,"remote_state":"not inspected; the server enforces fast-forward safety","warning":"Git configuration, credential helpers and SSH can execute code. Network output may contain secrets and is not returned. Git is not sandboxed or journaled."});
        self.reviews.insert(
            id,
            Review {
                repository: workspace.root().to_path_buf(),
                operation,
                snapshot: s,
            },
        );
        Ok(result)
    }
    pub(crate) fn apply(
        &mut self,
        workspace: &Workspace,
        id: u64,
        operation: Operation,
        source_commit: Option<String>,
    ) -> Result<Value> {
        // All four operations can write repository metadata (and switch also
        // writes files). A read-only primary project must not authorize them.
        workspace
            .access
            .authorize(&workspace.root().join(".git"), true)?;
        let r = self
            .reviews
            .remove(&id)
            .ok_or("unknown, expired or consumed Git operation review")?;
        if r.repository != workspace.root() {
            return Err(
                "Git operation review belongs to a different repository; review again".into(),
            );
        }
        if r.operation != operation {
            return Err("operation differs from reviewed request; review again".into());
        }
        if matches!(operation, Operation::Push { .. }) {
            if source_commit != r.snapshot.source {
                return Err(
                    "push must include the exact reviewed source_commit; review again".into(),
                );
            }
        } else if source_commit.is_some() {
            return Err("source_commit is only accepted for push".into());
        }
        if snapshot(workspace, &operation)? != r.snapshot {
            return Err(
                "stale Git operation review: HEAD, index, branch or configuration changed".into(),
            );
        }
        preflight(workspace, &operation, &r.snapshot)?;
        let _branch_lock = match &operation {
            Operation::Switch { branch } | Operation::Push { branch, .. } => {
                Some(lock_branch(workspace.root(), branch)?)
            }
            _ => None,
        };
        if snapshot(workspace, &operation)? != r.snapshot {
            return Err("stale Git operation review before execution".into());
        }
        let root = workspace.root();
        let args = match &operation {
            Operation::Create { branch } => vec![
                "branch".into(),
                "--".into(),
                branch.clone(),
                r.snapshot.head.0.clone().unwrap(),
            ],
            Operation::Switch { branch } => vec![
                "switch".into(),
                "--no-guess".into(),
                "--no-overwrite-ignore".into(),
                "--".into(),
                branch.clone(),
            ],
            Operation::Fetch { remote } => vec![
                "fetch".into(),
                "--no-tags".into(),
                "--no-prune".into(),
                "--no-recurse-submodules".into(),
                "--refmap=".into(),
                "--".into(),
                remote.clone(),
                format!("refs/heads/*:refs/remotes/{remote}/*"),
            ],
            Operation::Push {
                remote,
                remote_branch,
                ..
            } => vec![
                "push".into(),
                "--porcelain".into(),
                "--".into(),
                remote.clone(),
                format!(
                    "{}:refs/heads/{remote_branch}",
                    r.snapshot.source.as_ref().unwrap()
                ),
            ],
        };
        // The raw output can contain remote URLs and credential data. Never
        // expose either stream, including on errors or timeouts.
        let result = git::command_inner(root, &args, true)?;
        if result.timed_out {
            return Err("Git operation timed out; inspect local and remote state before retrying; output withheld".into());
        }
        if !result.status.success() {
            let text = format!(
                "{} {}",
                String::from_utf8_lossy(&result.stdout),
                String::from_utf8_lossy(&result.stderr)
            );
            if matches!(operation, Operation::Push { .. })
                && (text.contains("non-fast-forward") || text.contains("fetch first"))
            {
                return Err("push rejected as non-fast-forward; fetch and review before retrying; output withheld".into());
            }
            return Err(format!("Git operation failed (exit {:?}); inspect Git state; output withheld to protect credentials",result.status.code()).into());
        }
        if let Operation::Push {
            branch,
            remote,
            remote_branch,
            set_upstream: true,
        } = &operation
        {
            if oid(root, branch)? != r.snapshot.source {
                return Err("push succeeded, but the source branch changed before upstream setup; no upstream was set".into());
            }
            let upstream = format!("refs/remotes/{remote}/{remote_branch}");
            let result = git::command_inner(
                root,
                &[
                    "branch".into(),
                    "--set-upstream-to".into(),
                    upstream,
                    "--".into(),
                    branch.clone(),
                ],
                true,
            )?;
            if result.timed_out || !result.status.success() {
                return Err("push succeeded, but setting the upstream failed or timed out; inspect Git configuration before retrying; output withheld".into());
            }
        }
        Ok(
            json!({"operation":operation,"source_commit":r.snapshot.source,"success":true,"journaled":false,"output_withheld":true}),
        )
    }
}
