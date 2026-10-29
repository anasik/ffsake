//! Git review and explicit host-authorized build/test jobs. No implicit execution policy.
use crate::{
    Workspace,
    journal::Journal,
    workflow_process::{self, Output},
};
use serde::Deserialize;
use serde_json::{Value, json};
use std::{
    collections::BTreeMap,
    fs,
    io::Read,
    path::{Component, Path},
    process::Command,
    time::{Duration, Instant},
};
type Result<T> = std::result::Result<T, Box<dyn std::error::Error>>;
#[derive(Debug, Deserialize, serde::Serialize)]
#[serde(rename_all = "snake_case")]
pub enum Kind {
    Build,
    Test,
}
#[derive(Debug, Deserialize, serde::Serialize)]
#[serde(deny_unknown_fields)]
pub struct Task {
    kind: Kind,
    argv: Vec<String>,
    #[serde(default = "cwd")]
    cwd: String,
    #[serde(default = "timeout")]
    timeout_seconds: u64,
    #[serde(default = "output_limit")]
    output_bytes: usize,
}
fn cwd() -> String {
    ".".into()
}
fn timeout() -> u64 {
    60
}
fn output_limit() -> usize {
    65536
}
#[derive(Default, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Tasks {
    version: u32,
    tasks: BTreeMap<String, Task>,
}
impl Tasks {
    /// Only called with an explicit trusted startup argument, never a tool request.
    pub fn load(path: &Path) -> Result<Self> {
        let metadata = fs::metadata(path)?;
        if !metadata.is_file() || metadata.len() > 32768 {
            return Err("task policy must be a regular JSON file <=32 KiB".into());
        }
        let mut bytes = Vec::new();
        fs::File::open(path)?.take(32769).read_to_end(&mut bytes)?;
        if bytes.len() > 32768 {
            return Err("task policy exceeds 32 KiB".into());
        }
        let policy: Self = serde_json::from_slice(&bytes)?;
        if policy.version != 1 || policy.tasks.is_empty() || policy.tasks.len() > 32 {
            return Err("task policy requires version 1 and 1..32 named tasks".into());
        }
        for (name, task) in &policy.tasks {
            if name.is_empty()
                || name.len() > 64
                || !name
                    .bytes()
                    .all(|b| b.is_ascii_alphanumeric() || b"_-".contains(&b))
                || task.argv.is_empty()
                || task.argv.len() > 64
                || task.argv[0].is_empty()
                || task.argv.iter().any(|a| a.len() > 4096 || a.contains('\0'))
                || !(1..=300).contains(&task.timeout_seconds)
                || !(1..=262144).contains(&task.output_bytes)
            {
                return Err("invalid task name/argv/time/output bounds".into());
            }
            relative(&task.cwd, true)?;
        }
        Ok(policy)
    }
    pub(crate) fn restricted(&self) -> bool {
        !self.tasks.is_empty()
    }
    pub(crate) fn list(&self) -> Value {
        json!({"execution_enabled":!self.tasks.is_empty(),"tasks":self.tasks,"policy":"Only startup-configured argv; no request commands, args, environment or policy path. Jobs may change files and are not journal transactions."})
    }
    pub(crate) fn run(&self, workspace: &Workspace, name: &str) -> Result<Value> {
        let task = self
            .tasks
            .get(name)
            .ok_or("unknown or disabled task; host must configure --tasks at startup")?;
        let directory = if task.cwd == "." {
            workspace.root().to_path_buf()
        } else {
            Journal::open(workspace.root())?.resolve(&task.cwd)?
        };
        if !fs::symlink_metadata(&directory)?.is_dir() {
            return Err("configured task cwd must be a workspace directory".into());
        }
        let mut command = Command::new(&task.argv[0]);
        command.args(&task.argv[1..]).current_dir(directory);
        let output = workflow_process::run(
            &mut command,
            Duration::from_secs(task.timeout_seconds),
            task.output_bytes,
        )?;
        #[cfg(unix)]
        let signal = std::os::unix::process::ExitStatusExt::signal(&output.status);
        #[cfg(not(unix))]
        let signal: Option<i32> = None;
        Ok(
            json!({"task":name,"kind":task.kind,"exit_code":output.status.code(),"signal":signal,"success":output.status.success()&&!output.timed_out,"timed_out":output.timed_out,"output_truncated":output.truncated,"stdout":String::from_utf8_lossy(&output.stdout),"stderr":String::from_utf8_lossy(&output.stderr),"elapsed_ms":output.elapsed_ms,"journaled":false,"output_is_untrusted":true}),
        )
    }
}
fn relative(path: &str, root: bool) -> Result<()> {
    if root && path == "." {
        return Ok(());
    }
    if path.is_empty()
        || path.len() > 4096
        || Path::new(path)
            .components()
            .any(|p| !matches!(p, Component::Normal(_)))
        || path.split('/').any(|p| p == ".git" || p == ".ffsake")
    {
        return Err(
            "Git/task paths must be bounded workspace-relative paths outside protected metadata"
                .into(),
        );
    }
    Ok(())
}
fn git(workspace: &Workspace, args: &[String], deadline: Instant) -> Result<Output> {
    let mut command = Command::new("git");
    // Prevent inherited Git routing variables from redirecting inspection elsewhere.
    for (name, _) in std::env::vars_os() {
        if name.to_string_lossy().starts_with("GIT_") {
            command.env_remove(name);
        }
    }
    command
        .env("GIT_OPTIONAL_LOCKS", "0")
        .env("GIT_CONFIG_NOSYSTEM", "1")
        .env("GIT_CONFIG_GLOBAL", "/dev/null");
    command
        .current_dir(workspace.root())
        .args([
            "--no-pager",
            "-c",
            "core.fsmonitor=false",
            "-c",
            "core.untrackedCache=false",
            "-c",
            "core.quotePath=true",
        ])
        .args(args);
    let output = workflow_process::run(
        &mut command,
        deadline
            .checked_duration_since(Instant::now())
            .ok_or("Git inspection exceeded 15 seconds")?,
        262144,
    )?;
    if output.timed_out || output.truncated {
        return Err("Git inspection exceeded time/output bounds; narrow the request".into());
    }
    Ok(output)
}
fn checked(workspace: &Workspace, args: &[&str], deadline: Instant) -> Result<Vec<u8>> {
    let output = git(
        workspace,
        &args.iter().map(|s| s.to_string()).collect::<Vec<_>>(),
        deadline,
    )?;
    if !output.status.success() {
        return Err(format!(
            "Git inspection failed: {}",
            String::from_utf8_lossy(&output.stderr)
        )
        .into());
    }
    Ok(output.stdout)
}
#[derive(Debug, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum GitQuery {
    Status,
    Diff,
}
pub(crate) fn inspect(
    workspace: &Workspace,
    query: GitQuery,
    staged: bool,
    paths: &[String],
) -> Result<Value> {
    let deadline = Instant::now() + Duration::from_secs(15);
    // Even status/diff can invoke clean/process filters. Refuse those repository
    // configurations rather than execute code through a read-only tool.
    let filters = git(
        workspace,
        &[
            "config".into(),
            "--get-regexp".into(),
            "^filter\\..*\\.(clean|process)$".into(),
        ],
        deadline,
    )?;
    if filters.status.success() {
        return Err("read-only Git inspection refuses repository clean/process filters; use host Git for this configuration".into());
    }
    if filters.status.code() != Some(1) {
        return Err("cannot verify read-only Git configuration".into());
    }
    if paths.len() > 100 {
        return Err("Git inspection supports at most 100 explicit paths".into());
    }
    for p in paths {
        relative(p, false)?;
    }
    let top = checked(workspace, &["rev-parse", "--show-toplevel"], deadline)?;
    let top = std::str::from_utf8(&top)?.trim_end_matches('\n');
    if Path::new(top).canonicalize()? != workspace.root() {
        return Err("Git inspection requires the repository/worktree root as workspace".into());
    }
    let head = git(
        workspace,
        &["rev-parse".into(), "--verify".into(), "HEAD".into()],
        deadline,
    )?;
    let head = if head.status.success() {
        Some(String::from_utf8(head.stdout)?.trim().to_owned())
    } else {
        None
    };
    match query {
        GitQuery::Status => {
            if staged || !paths.is_empty() {
                return Err(
                    "status does not accept staged or paths; use diff for scoped content".into(),
                );
            }
            let bytes = checked(
                workspace,
                &[
                    "status",
                    "--porcelain=v1",
                    "-z",
                    "--untracked-files=normal",
                    "--ignore-submodules=all",
                ],
                deadline,
            )?;
            let mut records = bytes.split(|b| *b == 0).filter(|b| !b.is_empty());
            let mut entries = Vec::new();
            let mut excluded = 0;
            while let Some(record) = records.next() {
                if record.len() < 4 || record[2] != b' ' {
                    return Err("invalid Git status record".into());
                }
                let status = std::str::from_utf8(&record[..2])?;
                let path = std::str::from_utf8(&record[3..])?;
                let original = if status.contains(['R', 'C']) {
                    Some(std::str::from_utf8(
                        records.next().ok_or("missing rename source")?,
                    )?)
                } else {
                    None
                };
                if path.split('/').any(|p| p == ".ffsake" || p == ".git")
                    || original.is_some_and(|p| p.split('/').any(|p| p == ".ffsake" || p == ".git"))
                {
                    excluded += 1;
                    continue;
                }
                if entries.len() >= 1000 {
                    return Err("Git status exceeds 1000 entries".into());
                }
                entries.push(json!({"status":status,"path":path,"original_path":original}));
            }
            Ok(
                json!({"head":head,"entries":entries,"protected_entries_excluded":excluded,"read_only":true,"submodules":"ignored","untracked":"directory summaries","atomic_snapshot":false}),
            )
        }
        GitQuery::Diff => {
            let mut args = vec![
                "diff".into(),
                "--no-ext-diff".into(),
                "--no-textconv".into(),
                "--no-color".into(),
                "--ignore-submodules=all".into(),
            ];
            if staged {
                args.push("--cached".into());
            }
            args.push("--".into());
            if paths.is_empty() {
                args.push(".".into());
            } else {
                args.extend(paths.iter().map(|p| format!(":(literal){p}")));
            }
            args.extend([
                ":(exclude,glob)**/.ffsake".into(),
                ":(exclude,glob).ffsake".into(),
                ":(exclude,glob)**/.ffsake/**".into(),
                ":(exclude,glob).ffsake/**".into(),
            ]);
            let output = git(workspace, &args, deadline)?;
            if !output.status.success() {
                return Err(format!(
                    "Git diff failed: {}",
                    String::from_utf8_lossy(&output.stderr)
                )
                .into());
            }
            Ok(
                json!({"head":head,"staged":staged,"diff":String::from_utf8_lossy(&output.stdout),"read_only":true,"untracked_contents_included":false,"atomic_snapshot":false}),
            )
        }
    }
}
