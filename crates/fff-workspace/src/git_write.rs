//! Selected-file Git changes, guarded by resident-owned review snapshots.
use crate::{
    Workspace,
    workflow_process::{self, Output},
};
use serde_json::{Value, json};
use std::{
    collections::{BTreeMap, BTreeSet},
    fs,
    io::Read,
    path::{Component, Path, PathBuf},
    process::Command,
    time::Duration,
};

type Result<T> = std::result::Result<T, Box<dyn std::error::Error>>;
const OUTPUT: usize = 262144;

fn command_with_index(
    root: &Path,
    args: &[String],
    mutate: bool,
    index: Option<&Path>,
) -> Result<Output> {
    for (key, _) in std::env::vars_os() {
        if key.to_string_lossy().starts_with("GIT_") && key != "GIT_PAGER" {
            return Err(format!("Git review/mutation refuses inherited {}; ask the host to clear Git environment overrides", key.to_string_lossy()).into());
        }
    }
    let mut cmd = Command::new("git");
    cmd.current_dir(root).arg("--no-pager");
    if let Some(index) = index {
        cmd.env("GIT_INDEX_FILE", index);
    }
    // Inspections run before we can inspect repository config. Never execute an
    // unreviewed fsmonitor helper while determining whether mutation is safe.
    // Mutations preserve the user's configuration and reject unsafe helpers.
    if !mutate && args.first().map(String::as_str) != Some("config") {
        cmd.env("GIT_OPTIONAL_LOCKS", "0");
        cmd.args([
            "-c",
            "core.fsmonitor=false",
            "-c",
            "core.untrackedCache=false",
        ]);
    }
    cmd.args(args);
    workflow_process::run(&mut cmd, Duration::from_secs(30), OUTPUT)
}
fn command_inner(root: &Path, args: &[String], mutate: bool) -> Result<Output> {
    command_with_index(root, args, mutate, None)
}
fn command(root: &Path, args: &[String]) -> Result<Output> {
    command_inner(root, args, false)
}
fn run(root: &Path, args: &[&str]) -> Result<Output> {
    command(root, &args.iter().map(|s| (*s).into()).collect::<Vec<_>>())
}
fn good(out: Output) -> Result<Vec<u8>> {
    if out.timed_out {
        return Err("Git timed out; inspect HEAD, index and worktree before retrying because the outcome may be uncertain".into());
    }
    if out.truncated {
        return Err(
            "Git output exceeded 256 KiB; narrow the request and inspect state before retrying"
                .into(),
        );
    }
    if !out.status.success() {
        return Err(format!(
            "Git failed (exit {:?}): {}",
            out.status.code(),
            String::from_utf8_lossy(&out.stderr)
        )
        .into());
    }
    Ok(out.stdout)
}
fn config(root: &Path, key: &str) -> Result<Option<String>> {
    let out = run(root, &["config", "--get", key])?;
    if out.timed_out || out.truncated {
        return Err("cannot safely inspect Git configuration".into());
    }
    match out.status.code() {
        Some(0) => Ok(Some(
            String::from_utf8(out.stdout)?
                .trim_end_matches('\n')
                .to_owned(),
        )),
        Some(1) => Ok(None),
        _ => Err("cannot inspect Git configuration".into()),
    }
}
fn path_check(p: &str) -> Result<()> {
    if p.is_empty()
        || p.len() > 4096
        || p.contains('\0')
        || Path::new(p).is_absolute()
        || Path::new(p)
            .components()
            .any(|c| !matches!(c, Component::Normal(_)))
        || p.split('/').any(|x| x == ".git" || x == ".ffsake")
    {
        return Err("select exact workspace-relative files outside protected metadata".into());
    }
    Ok(())
}
fn pathspec(p: &str) -> String {
    format!(":(literal){p}")
}
fn root_check(root: &Path) -> Result<()> {
    let top = good(run(root, &["rev-parse", "--show-toplevel"])?)?;
    let top = std::str::from_utf8(&top)?.trim_end_matches('\n');
    if Path::new(top).canonicalize()? != root {
        return Err("Git changes require the repository/worktree root as workspace".into());
    }
    Ok(())
}
fn head(root: &Path) -> Result<(Option<String>, Option<String>)> {
    let out = run(root, &["rev-parse", "--verify", "HEAD"])?;
    if out.timed_out || out.truncated {
        return Err("cannot inspect HEAD".into());
    }
    let value = if out.status.success() {
        Some(String::from_utf8(out.stdout)?.trim().to_owned())
    } else {
        None
    };
    let sym = run(root, &["symbolic-ref", "--quiet", "HEAD"])?;
    if sym.timed_out || sym.truncated {
        return Err("cannot inspect HEAD reference".into());
    }
    let reference = if sym.status.success() {
        Some(String::from_utf8(sym.stdout)?.trim().to_owned())
    } else {
        None
    };
    if value.is_none() && reference.is_none() {
        return Err("HEAD is invalid, not an unborn branch".into());
    }
    Ok((value, reference))
}
fn git_path(root: &Path, name: &str) -> Result<PathBuf> {
    let bytes = good(run(
        root,
        &["rev-parse", "--path-format=absolute", "--git-path", name],
    )?)?;
    Ok(PathBuf::from(
        std::str::from_utf8(&bytes)?.trim_end_matches('\n'),
    ))
}
fn hash_file(path: &Path, max: u64) -> Result<Option<String>> {
    let meta = match fs::symlink_metadata(path) {
        Ok(m) => m,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(None),
        Err(e) => return Err(e.into()),
    };
    if !meta.is_file() || meta.file_type().is_symlink() || meta.len() > max {
        return Err(format!("expected a regular file <= {max} bytes: {}", path.display()).into());
    }
    let mut file = fs::File::open(path)?;
    let mut h = blake3::Hasher::new();
    let mut buf = [0; 16384];
    let mut n = 0u64;
    loop {
        let read = file.read(&mut buf)?;
        if read == 0 {
            break;
        }
        n += read as u64;
        if n > max {
            return Err("file grew beyond limit".into());
        }
        h.update(&buf[..read]);
    }
    if n != meta.len() {
        return Err("file changed during review".into());
    }
    #[cfg(unix)]
    let mode = {
        use std::os::unix::fs::PermissionsExt;
        meta.permissions().mode() & 0o777
    };
    #[cfg(not(unix))]
    let mode = 0;
    Ok(Some(format!("{}:{mode:o}:{}", h.finalize(), n)))
}
fn hooks(root: &Path) -> Result<()> {
    let directory = git_path(root, "hooks")?;
    for name in [
        "pre-commit",
        "prepare-commit-msg",
        "commit-msg",
        "post-commit",
        "post-rewrite",
        "post-index-change",
        "reference-transaction",
    ] {
        let path = directory.join(name);
        match fs::metadata(&path) {
            Ok(m) if m.is_file() => {
                #[cfg(unix)]
                {
                    use std::os::unix::fs::PermissionsExt;
                    if m.permissions().mode() & 0o111 != 0 {
                        return Err(format!("Git hook {} is executable; FFSAKE cannot guarantee reviewed changes with hooks. Use host Git; hooks are not disabled",path.display()).into());
                    }
                }
                #[cfg(not(unix))]
                {
                    return Err("configured Git hooks require host Git".into());
                }
            }
            Ok(_) => return Err("Git hook path is not a regular file; use host Git".into()),
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => {}
            Err(e) => return Err(e.into()),
        }
    }
    Ok(())
}
fn helpers(root: &Path, paths: &[String], commit: bool) -> Result<()> {
    if let Some(v) = config(root, "core.fsmonitor")?
        && !["false", "no", "off", "0", ""].contains(&v.to_ascii_lowercase().as_str())
    {
        return Err(
            "Git fsmonitor may execute a helper; use host Git instead of silently disabling it"
                .into(),
        );
    }
    if commit {
        let sign = run(root, &["config", "--bool", "--get", "commit.gpgsign"])?;
        if sign.timed_out || sign.truncated || !matches!(sign.status.code(), Some(0 | 1)) {
            return Err("cannot verify Git commit signing configuration".into());
        }
        if sign.status.success() && std::str::from_utf8(&sign.stdout)?.trim() == "true" {
            return Err(
                "Git commit signing is enabled; use host Git. FFSAKE does not disable signing"
                    .into(),
            );
        }
    }
    hooks(root)?;
    // Sentinel strings can also be actual filter names. Query each value,
    // including "set", "unset", and "unspecified", rather than assuming
    // that those values cannot identify an executable configured driver.
    for path in paths {
        for cached in [false, true] {
            let mut args = vec!["check-attr".into(), "-z".into()];
            if cached {
                args.push("--cached".into());
            }
            args.extend(["filter".into(), "--".into(), path.clone()]);
            let bytes = good(command(root, &args)?)?;
            let fields = bytes.split(|b| *b == 0).collect::<Vec<_>>();
            let value =
                std::str::from_utf8(fields.get(2).ok_or("invalid Git attribute response")?)?;
            for suffix in ["clean", "process"] {
                if config(root, &format!("filter.{value}.{suffix}"))?.is_some() {
                    return Err(format!(
                        "selected file {path:?} may use executable Git filter {value}; use host Git"
                    )
                    .into());
                }
            }
        }
    }
    Ok(())
}
#[derive(Clone, PartialEq, Eq)]
struct Snapshot {
    head: (Option<String>, Option<String>),
    index: Option<String>,
    config: String,
    files: Vec<Option<String>>,
    attributes: String,
}
fn snapshot(workspace: &Workspace, paths: &[String], write: bool) -> Result<Snapshot> {
    let root = workspace.root();
    root_check(root)?;
    let head = head(root)?;
    let index = hash_file(&git_path(root, "index")?, 32 * 1024 * 1024)?;
    let cfg = good(run(root, &["config", "--list", "--null"])?)?;
    let mut attrs = blake3::Hasher::new();
    let mut files = Vec::new();
    for p in paths {
        path_check(p)?;
        let (resolved, owner) = workspace.access.authorize(Path::new(p), write)?;
        if owner != root {
            return Err("Git paths must belong to the primary project".into());
        }
        files.push(hash_file(&resolved, 32 * 1024 * 1024)?);
        // Literal pathspecs can still select descendants when naming a directory.
        let listed = good(command(
            root,
            &["ls-files".into(), "-z".into(), "--".into(), pathspec(p)],
        )?)?;
        for name in listed.split(|b| *b == 0).filter(|x| !x.is_empty()) {
            if name != p.as_bytes() {
                return Err(
                    "only exact files may be selected, not directories or submodules".into(),
                );
            }
        }
        let a = good(command(
            root,
            &[
                "check-attr".into(),
                "-z".into(),
                "--all".into(),
                "--".into(),
                p.clone(),
            ],
        )?)?;
        attrs.update(&a);
    }
    Ok(Snapshot {
        head,
        index,
        config: blake3::hash(&cfg).to_hex().to_string(),
        files,
        attributes: attrs.finalize().to_hex().to_string(),
    })
}
fn diff(root: &Path, paths: &[String], cached: bool) -> Result<String> {
    let mut args = vec![
        "diff".into(),
        "--no-ext-diff".into(),
        "--no-textconv".into(),
        "--no-color".into(),
        "--binary".into(),
        "--full-index".into(),
        "--no-renames".into(),
        "--ignore-submodules=none".into(),
    ];
    if cached {
        args.push("--cached".into());
    }
    args.push("--".into());
    args.extend(paths.iter().map(|p| pathspec(p)));
    Ok(String::from_utf8(good(command(root, &args)?)?)?)
}
// A plain `git diff` trusts assume-unchanged / skip-worktree and can miss an
// edit that `git commit --only` will nevertheless stage. Compare the actual
// normalized worktree blob with the index entry before invoking commit.
fn ensure_index_matches_worktree(root: &Path, paths: &[String]) -> Result<()> {
    for path in paths {
        let flags = good(command(
            root,
            &[
                "ls-files".into(),
                "-v".into(),
                "-z".into(),
                "--".into(),
                pathspec(path),
            ],
        )?)?;
        for entry in flags.split(|b| *b == 0).filter(|e| !e.is_empty()) {
            if entry.len() < 3
                || entry[0] != b'H'
                || entry[1] != b' '
                || &entry[2..] != path.as_bytes()
            {
                return Err(
                    "selected file has an unsupported index flag or conflict; use host Git".into(),
                );
            }
        }
        let index = good(command(
            root,
            &[
                "ls-files".into(),
                "--stage".into(),
                "-z".into(),
                "--".into(),
                pathspec(path),
            ],
        )?)?;
        let entries = index
            .split(|b| *b == 0)
            .filter(|e| !e.is_empty())
            .collect::<Vec<_>>();
        let file = root.join(path);
        match (entries.as_slice(), file.exists()) {
            ([], false) => {},
            ([entry], true) => {
                let text = std::str::from_utf8(entry)?;
                let (metadata, name) = text.split_once('\t').ok_or("invalid Git index entry")?;
                let parts = metadata.split(' ').collect::<Vec<_>>();
                if name != path || parts.len() != 3 || parts[2] != "0" || !["100644", "100755"].contains(&parts[0]) {
                    return Err("selected file has an unsupported index entry; use host Git".into());
                }
                let object = good(command(root, &["hash-object".into(), format!("--path={path}"), "--".into(), path.clone()])?)?;
                if std::str::from_utf8(&object)?.trim() != parts[1] {
                    return Err("selected worktree content differs from the reviewed index; stage it and review again".into());
                }
            },
            _ => return Err("selected worktree content differs from the reviewed index; stage it and review again".into()),
        }
    }
    Ok(())
}
// Construct a tree from HEAD and the selected reviewed index entries. The
// real index is never rewritten by commit, and update-ref compares the old
// branch ID atomically. This avoids `git commit --only` re-reading worktree
// files after review and accidentally committing a concurrent edit.
fn commit_reviewed(workspace: &Workspace, review: &Review, message: &str) -> Result<Value> {
    let root = workspace.root();
    let reference = review
        .snapshot
        .head
        .1
        .as_ref()
        .ok_or("detached HEAD commits are not supported; use host Git")?;
    let internal = root.join(".ffsake");
    crate::access::no_symlinks(&internal)?;
    fs::create_dir_all(&internal)?;
    crate::access::no_symlinks(&internal)?;
    let scratch = tempfile::Builder::new()
        .prefix("git-commit-")
        .tempdir_in(&internal)?;
    let index = scratch.path().join("index");
    let mut read_tree = vec!["read-tree".to_string()];
    if let Some(old) = &review.snapshot.head.0 {
        read_tree.push(old.clone());
    } else {
        read_tree.push("--empty".into());
    }
    good(command_with_index(root, &read_tree, false, Some(&index))?)?;
    for path in &review.paths {
        let entries = good(command(
            root,
            &[
                "ls-files".into(),
                "--stage".into(),
                "-z".into(),
                "--".into(),
                pathspec(path),
            ],
        )?)?;
        let entries = entries
            .split(|b| *b == 0)
            .filter(|e| !e.is_empty())
            .collect::<Vec<_>>();
        if entries.is_empty() {
            good(command_with_index(
                root,
                &[
                    "update-index".into(),
                    "--force-remove".into(),
                    "--".into(),
                    path.clone(),
                ],
                true,
                Some(&index),
            )?)?;
        } else if let [entry] = entries.as_slice() {
            let (metadata, name) = std::str::from_utf8(entry)?
                .split_once('\t')
                .ok_or("invalid index entry")?;
            let parts = metadata.split(' ').collect::<Vec<_>>();
            if name != path
                || parts.len() != 3
                || parts[2] != "0"
                || !["100644", "100755"].contains(&parts[0])
            {
                return Err("selected path has an unsupported index entry".into());
            }
            good(command_with_index(
                root,
                &[
                    "update-index".into(),
                    "--add".into(),
                    "--cacheinfo".into(),
                    parts[0].into(),
                    parts[1].into(),
                    path.clone(),
                ],
                true,
                Some(&index),
            )?)?;
        } else {
            return Err("selected path has unresolved index conflicts".into());
        }
    }
    let tree = String::from_utf8(good(command_with_index(
        root,
        &["write-tree".into()],
        true,
        Some(&index),
    )?)?)?
    .trim()
    .to_owned();
    let mut args = vec!["commit-tree".into(), tree];
    if let Some(old) = &review.snapshot.head.0 {
        args.extend(["-p".into(), old.clone()]);
    }
    args.extend(["-m".into(), message.into()]);
    let commit = String::from_utf8(good(command_inner(root, &args, true)?)?)?
        .trim()
        .to_owned();
    helpers(root, &review.paths, true)?;
    if snapshot(workspace, &review.paths, true)? != review.snapshot {
        return Err("stale Git review before updating the branch; no commit was published".into());
    }
    let old = if let Some(old) = &review.snapshot.head.0 {
        old.clone()
    } else {
        let format = String::from_utf8(good(run(root, &["rev-parse", "--show-object-format"])?)?)?;
        match format.trim() {
            "sha1" => "0".repeat(40),
            "sha256" => "0".repeat(64),
            _ => return Err("unsupported Git object format".into()),
        }
    };
    let update = vec![
        "update-ref".into(),
        "-m".into(),
        "commit: FFSAKE reviewed changes".into(),
        reference.clone(),
        commit.clone(),
        old,
    ];
    let out = command_inner(root, &update, true)?;
    if out.timed_out || !out.status.success() {
        return Err(format!(
            "branch update failed or outcome uncertain; inspect Git state: {}",
            String::from_utf8_lossy(&out.stderr)
        )
        .into());
    }
    Ok(json!({"action":"commit","paths":review.paths,"head":commit,"journaled":false}))
}
struct Review {
    paths: Vec<String>,
    snapshot: Snapshot,
}
#[derive(Default)]
pub(crate) struct Reviews {
    next: u64,
    entries: BTreeMap<u64, Review>,
}
impl Reviews {
    pub(crate) fn review(&mut self, workspace: &Workspace, paths: Vec<String>) -> Result<Value> {
        if paths.is_empty()
            || paths.len() > 100
            || paths.iter().collect::<BTreeSet<_>>().len() != paths.len()
        {
            return Err("select 1..100 distinct exact files".into());
        }
        let before = snapshot(workspace, &paths, false)?;
        helpers(workspace.root(), &paths, false)?;
        let staged = diff(workspace.root(), &paths, true)?;
        let unstaged = diff(workspace.root(), &paths, false)?;
        let after = snapshot(workspace, &paths, false)?;
        if before != after {
            return Err(format!("Git state changed during review (head={}, index={}, config={}, files={}, attributes={}); retry", before.head != after.head, before.index != after.index, before.config != after.config, before.files != after.files, before.attributes != after.attributes).into());
        }
        if self.entries.len() >= 64 {
            self.entries.pop_first();
        }
        self.next += 1;
        let id = self.next;
        self.entries.insert(
            id,
            Review {
                paths: paths.clone(),
                snapshot: before.clone(),
            },
        );
        Ok(
            json!({"review":id,"head":before.head.0,"paths":paths,"staged_diff":staged,"unstaged_diff":unstaged,"worktree_hashes":before.files,"untracked_content_in_diff":false,"journaled":false,"warning":"Review is scoped to selected files. Mutations may execute Git and are not editor transactions."}),
        )
    }
    pub(crate) fn mutate(
        &mut self,
        workspace: &Workspace,
        id: u64,
        action: &str,
        message: Option<String>,
    ) -> Result<Value> {
        let review = self
            .entries
            .remove(&id)
            .ok_or("unknown, expired or consumed Git review")?;
        if !["stage", "unstage", "commit"].contains(&action) {
            return Err("unsupported Git action".into());
        }
        helpers(workspace.root(), &review.paths, action == "commit")?;
        let now = snapshot(workspace, &review.paths, true)?;
        if now != review.snapshot {
            return Err("stale Git review: HEAD, index, selected content, attributes or Git configuration changed; review again".into());
        }
        let root = workspace.root();
        if action == "commit" {
            let msg = message.ok_or("commit message is required")?;
            if msg.trim().is_empty() || msg.len() > 16384 || msg.contains('\0') {
                return Err("commit message must be nonempty and <=16 KiB".into());
            }
            if diff(root, &review.paths, true)?.is_empty() {
                return Err("selected files have no staged changes".into());
            }
            if !diff(root, &review.paths, false)?.is_empty() {
                return Err("selected files have unstaged changes; stage them or use host Git for partial staging".into());
            }
            ensure_index_matches_worktree(root, &review.paths)?;
            if snapshot(workspace, &review.paths, true)? != review.snapshot {
                return Err("stale Git review before commit".into());
            }
            return commit_reviewed(workspace, &review, &msg);
        }
        if message.is_some() {
            return Err("message is only accepted for commit".into());
        }
        let mut args = if action == "stage" {
            vec!["add".into(), "--".into()]
        } else if now.head.0.is_none() {
            vec!["reset".into(), "--quiet".into(), "--".into()]
        } else {
            vec!["restore".into(), "--staged".into(), "--".into()]
        };
        args.extend(review.paths.iter().map(|p| pathspec(p)));
        good(command_inner(root, &args, true)?)?;
        Ok(
            json!({"action":action,"paths":review.paths,"journaled":false,"next":"review again before the next mutation"}),
        )
    }
}
