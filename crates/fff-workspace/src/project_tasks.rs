//! A narrow VS Code process-task subset with project-bound persistent approvals.
use crate::{Workspace, access::no_symlinks, workflow_process};
use jsonc_parser::{
    ParseOptions,
    cst::{CstInputValue, CstRootNode},
};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
#[cfg(unix)]
use std::os::unix::fs::FileExt;
#[cfg(windows)]
use std::os::windows::fs::FileExt;
use std::{
    collections::BTreeMap,
    fs::{self, File},
    io::{Read, Write},
    path::{Component, Path, PathBuf},
    process::Command,
    sync::{
        Arc, Condvar, Mutex,
        atomic::{AtomicBool, Ordering},
    },
    thread::JoinHandle,
    time::{Duration, Instant},
};
type Result<T> = std::result::Result<T, Box<dyn std::error::Error>>;
const MAX: u64 = 131072;
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Definition {
    pub label: String,
    #[serde(rename = "type")]
    pub kind: String,
    pub command: String,
    #[serde(default)]
    pub args: Vec<String>,
    #[serde(default)]
    pub options: Options,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub group: Option<String>,
}
#[derive(Clone, Debug, Default, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Options {
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub cwd: Option<String>,
}
#[derive(Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Approval {
    fingerprint: String,
    executable: PathBuf,
    args: Vec<String>,
    cwd: PathBuf,
    timeout_seconds: u64,
    output_bytes: usize,
}
#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Store {
    version: u32,
    project: PathBuf,
    tasks: BTreeMap<String, Approval>,
}
#[derive(Clone)]
struct Launch {
    executable: PathBuf,
    args: Vec<String>,
    cwd: PathBuf,
    fingerprint: String,
}
fn file_path(root: &Path) -> PathBuf {
    root.join(".vscode/tasks.json")
}
fn dir(root: &Path) -> PathBuf {
    root.join(".ffsake/project-tasks")
}
fn read_bounded(path: &Path) -> Result<Option<Vec<u8>>> {
    no_symlinks(path)?;
    let meta = match fs::symlink_metadata(path) {
        Ok(m) => m,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(None),
        Err(e) => return Err(e.into()),
    };
    if !meta.is_file() || meta.len() > MAX {
        return Err("task file/store must be a regular file <=128 KiB".into());
    }
    let mut bytes = Vec::new();
    fs::File::open(path)?
        .take(MAX + 1)
        .read_to_end(&mut bytes)?;
    if bytes.len() as u64 > MAX {
        return Err("task file/store exceeded size limit".into());
    }
    Ok(Some(bytes))
}
fn jsonc_options() -> ParseOptions {
    ParseOptions {
        allow_comments: true,
        allow_trailing_commas: true,
        allow_loose_object_property_names: false,
        allow_missing_commas: false,
        allow_single_quoted_strings: false,
        allow_hexadecimal_numbers: false,
        allow_unary_plus_numbers: false,
        allow_bare_decimal_point_numbers: false,
        allow_non_finite_numbers: false,
        allow_extended_string_escapes: false,
    }
}
fn input(value: Value) -> Result<CstInputValue> {
    Ok(match value {
        Value::Null => CstInputValue::Null,
        Value::Bool(b) => CstInputValue::Bool(b),
        Value::Number(n) => CstInputValue::Number(n.to_string()),
        Value::String(s) => CstInputValue::String(s),
        Value::Array(a) => CstInputValue::Array(a.into_iter().map(input).collect::<Result<_>>()?),
        Value::Object(o) => CstInputValue::Object(
            o.into_iter()
                .map(|(k, v)| Ok((k, input(v)?)))
                .collect::<Result<_>>()?,
        ),
    })
}
fn task_file(root: &Path) -> Result<(Value, Option<Vec<u8>>)> {
    let bytes = read_bounded(&file_path(root))?;
    let v: Value = if let Some(b) = &bytes {
        let text = std::str::from_utf8(b)?;
        jsonc_parser::parse_to_serde_value(text, &jsonc_options())
            .map_err(|e| format!("invalid VS Code JSONC task file: {e}"))?
    } else {
        json!({"version":"2.0.0","tasks":[]})
    };
    let o = v.as_object().ok_or("tasks.json must be an object")?;
    if o.keys().any(|k| k != "version" && k != "tasks")
        || v["version"] != "2.0.0"
        || !v["tasks"].is_array()
    {
        return Err(
            "unsupported tasks.json global features; expected only version 2.0.0 and tasks array"
                .into(),
        );
    }
    if v["tasks"].as_array().unwrap().len() > 64 {
        return Err("tasks.json supports at most 64 entries".into());
    }
    Ok((v, bytes))
}
fn render_definition(
    before: Option<&[u8]>,
    d: &Definition,
    index: Option<usize>,
    previous: Option<&Value>,
) -> Result<Vec<u8>> {
    let fallback = "{\n  \"version\": \"2.0.0\",\n  \"tasks\": []\n}\n";
    let text = match before {
        Some(bytes) => std::str::from_utf8(bytes)?,
        None => fallback,
    };
    let doc = CstRootNode::parse(text, &jsonc_options())?;
    let array = doc
        .object_value()
        .and_then(|o| o.array_value("tasks"))
        .ok_or("missing tasks array")?;
    let new_value = serde_json::to_value(d)?;
    if let Some(i) = index {
        let object = array
            .elements()
            .get(i)
            .and_then(|n| n.as_object())
            .ok_or("selected task must be an object")?;
        let mut fields = new_value.as_object().ok_or("invalid task")?.clone();
        for prop in object.properties() {
            let key = prop.decoded_name().ok_or("invalid task property")?;
            if let Some(value) = fields.remove(&key) {
                if previous.and_then(|old| old.get(&key)) != Some(&value) {
                    prop.set_value(input(value)?);
                }
            } else {
                prop.remove();
            }
        }
        for (key, value) in fields {
            object.append(&key, input(value)?);
        }
    } else {
        array.append(input(new_value)?);
    }
    Ok(doc.to_string().into_bytes())
}
fn store(root: &Path) -> Result<Store> {
    let p = dir(root).join("approvals.json");
    let value = match read_bounded(&p)? {
        Some(b) => serde_json::from_slice::<Store>(&b)?,
        None => Store {
            version: 1,
            project: root.to_path_buf(),
            tasks: BTreeMap::new(),
        },
    };
    if value.version != 1 || value.project != root || value.tasks.len() > 64 {
        return Err(
            "task authorization store is invalid or belongs to another canonical project".into(),
        );
    }
    Ok(value)
}
fn lock(root: &Path) -> Result<fs::File> {
    let path = dir(root);
    no_symlinks(&path)?;
    fs::create_dir_all(&path)?;
    no_symlinks(&path)?;
    let lock = path.join("store.lock");
    no_symlinks(&lock)?;
    let mut o = fs::OpenOptions::new();
    o.read(true).write(true).create(true).truncate(false);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        o.mode(0o600);
    }
    let f = o.open(lock)?;
    if !f.metadata()?.is_file() {
        return Err("task lock must be regular".into());
    }
    let start = Instant::now();
    loop {
        match f.try_lock() {
            Ok(()) => return Ok(f),
            Err(std::fs::TryLockError::WouldBlock) if start.elapsed() < Duration::from_secs(2) => {
                std::thread::sleep(Duration::from_millis(10))
            }
            Err(e) => return Err(format!("task authorization store busy: {e}").into()),
        }
    }
}
fn save(path: &Path, bytes: &[u8]) -> Result<()> {
    if bytes.len() as u64 > MAX {
        return Err("task file/store exceeds 128 KiB".into());
    }
    no_symlinks(path)?;
    if let Ok(m) = fs::symlink_metadata(path)
        && !m.is_file()
    {
        return Err("task destination must be regular".into());
    }
    let parent = path.parent().ok_or("missing parent")?;
    let permissions = fs::metadata(path).ok().map(|m| m.permissions());
    let mut f = tempfile::NamedTempFile::new_in(parent)?;
    if let Some(permissions) = permissions {
        f.as_file().set_permissions(permissions)?;
    }
    f.write_all(bytes)?;
    f.as_file().sync_all()?;
    f.persist(path)?;
    fs::File::open(parent)?.sync_all()?;
    Ok(())
}
fn resolve(
    workspace: &Workspace,
    d: &Definition,
    timeout: u64,
    limit: usize,
    approved: Option<&Approval>,
) -> Result<Launch> {
    if d.label.is_empty()
        || d.label.len() > 128
        || d.label.contains('\0')
        || d.kind != "process"
        || d.command.is_empty()
        || d.command.len() > 4096
        || d.command.contains('\0')
        || d.command.contains("${")
        || d.args.len() > 63
        || d.args
            .iter()
            .any(|x| x.len() > 4096 || x.contains('\0') || x.contains("${"))
        || !(1..=300).contains(&timeout)
        || !(1..=262144).contains(&limit)
        || d.group
            .as_deref()
            .is_some_and(|g| g != "build" && g != "test")
    {
        return Err("unsupported task: use type process, literal executable/args, group build/test or no group, and bounded limits".into());
    }
    let cwd = d.options.cwd.as_deref().unwrap_or("${workspaceFolder}");
    let relative = if cwd == "${workspaceFolder}" || cwd == "." {
        ""
    } else if let Some(p) = cwd.strip_prefix("${workspaceFolder}/") {
        p
    } else {
        cwd
    };
    if relative.contains("${")
        || Path::new(relative).is_absolute()
        || Path::new(relative)
            .components()
            .any(|p| !matches!(p, Component::Normal(_)))
        || relative.split('/').any(|p| p == ".git" || p == ".ffsake")
    {
        return Err("unsupported cwd or substitution; use ${workspaceFolder} or a workspace-relative directory".into());
    }
    let cwd = workspace.root().join(relative);
    workspace.access.authorize(&cwd, false)?;
    if !fs::symlink_metadata(&cwd)?.is_dir() {
        return Err("task cwd must be an existing authorized directory".into());
    }
    if approved.is_some_and(|a| a.args != d.args || a.cwd != cwd) {
        return Err("task arguments or cwd changed; host must reauthorize".into());
    }
    let executable = if d.command.contains('/') {
        let p = Path::new(&d.command);
        if p.is_absolute() {
            p.to_path_buf()
        } else {
            cwd.join(p)
        }
    } else {
        let mut found = None;
        for base in std::env::split_paths(&std::env::var_os("PATH").unwrap_or_default()) {
            let p = base.join(&d.command);
            if p.is_file() {
                found = Some(p);
                break;
            }
        }
        found.ok_or("task executable not found in PATH")?
    };
    let executable = executable.canonicalize()?;
    if approved.is_some_and(|a| a.executable != executable) {
        return Err("resolved executable changed; host must reauthorize".into());
    }
    let meta = fs::metadata(&executable)?;
    if !meta.is_file() || meta.len() > 256 * 1024 * 1024 {
        return Err("task executable must be a regular file <=256 MiB".into());
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        if meta.permissions().mode() & 0o111 == 0 {
            return Err("task executable is not executable".into());
        }
    }
    let mut hash = blake3::Hasher::new();
    let mut f = fs::File::open(&executable)?;
    let mut buf = [0; 16384];
    let mut n = 0u64;
    loop {
        let k = f.read(&mut buf)?;
        if k == 0 {
            break;
        }
        n += k as u64;
        if n > 256 * 1024 * 1024 {
            return Err("task executable exceeded size limit".into());
        }
        hash.update(&buf[..k]);
    }
    if n != meta.len() {
        return Err("task executable changed during inspection".into());
    }
    let payload = json!({"version":1,"executable":executable,"executable_hash":hash.finalize().to_hex().to_string(),"args":d.args,"cwd":cwd,"timeout_seconds":timeout,"output_bytes":limit});
    Ok(Launch {
        executable,
        args: d.args.clone(),
        cwd,
        fingerprint: blake3::hash(&serde_json::to_vec(&payload)?)
            .to_hex()
            .to_string(),
    })
}
fn find(v: &Value, label: &str) -> Result<Definition> {
    let matches = v["tasks"]
        .as_array()
        .unwrap()
        .iter()
        .filter(|x| x["label"].as_str() == Some(label))
        .collect::<Vec<_>>();
    if matches.len() != 1 {
        return Err("task label missing or duplicated".into());
    }
    serde_json::from_value(matches[0].clone())
        .map_err(|e| format!("unsupported task definition: {e}").into())
}
pub(crate) fn list(workspace: &Workspace) -> Result<Value> {
    let root = workspace.root();
    workspace.access.authorize(&file_path(root), false)?;
    let (v, _) = task_file(root)?;
    let approvals = store(root)?;
    let mut tasks = Vec::new();
    for raw in v["tasks"].as_array().unwrap() {
        let label = raw["label"].as_str().unwrap_or("");
        let record = approvals.tasks.get(label);
        let details = (|| -> Result<(Definition, bool, Option<Launch>)> {
            let d = find(&v, label)?;
            if d.kind != "process"
                || d.command.contains("${")
                || d.args.iter().any(|a| a.contains("${"))
                || d.group
                    .as_deref()
                    .is_some_and(|g| g != "build" && g != "test")
                || d.options.cwd.as_deref().is_some_and(|cwd| {
                    cwd.contains("${")
                        && cwd != "${workspaceFolder}"
                        && !cwd.starts_with("${workspaceFolder}/")
                })
            {
                return Err("unsupported task type, group or substitution".into());
            }
            let Some(record) = record else {
                return Ok((d, false, None));
            };
            let l = resolve(
                workspace,
                &d,
                record.timeout_seconds,
                record.output_bytes,
                Some(record),
            )?;
            let approved = record.fingerprint == l.fingerprint;
            Ok((d, approved, Some(l)))
        })();
        match details {
            Ok((d, approved, launch)) => tasks.push(json!({"definition":d,"authorized":approved,"resolved_executable":launch.as_ref().map(|l| &l.executable),"resolved_cwd":launch.as_ref().map(|l| &l.cwd),"timeout_seconds":record.map(|a|a.timeout_seconds),"output_bytes":record.map(|a|a.output_bytes)})),
            Err(e) => {
                tasks.push(json!({"definition":raw,"authorized":false,"unsupported":e.to_string()}))
            }
        }
    }
    Ok(
        json!({"execution_enabled":true,"mode":"project_tasks","tasks":tasks,"authorization":"host-approved fingerprints; tool invocation approval is separate"}),
    )
}
pub(crate) fn register(
    workspace: &Workspace,
    d: Definition,
    timeout: u64,
    limit: usize,
) -> Result<Value> {
    let root = workspace.root();
    let path = file_path(root);
    workspace.access.authorize(&path, true)?;
    let launch = resolve(workspace, &d, timeout, limit, None)?;
    let _lock = lock(root)?;
    let (mut v, before) = task_file(root)?;
    let entries = v["tasks"].as_array_mut().unwrap();
    let indices = entries
        .iter()
        .enumerate()
        .filter(|(_, x)| x["label"].as_str() == Some(&d.label))
        .map(|(i, _)| i)
        .collect::<Vec<_>>();
    if indices.len() > 1 {
        return Err("duplicate task label".into());
    }
    let previous = indices.first().map(|i| entries[*i].clone());
    if let Some(i) = indices.first() {
        entries[*i] = serde_json::to_value(&d)?;
    } else {
        if entries.len() >= 64 {
            return Err("task limit reached".into());
        }
        entries.push(serde_json::to_value(&d)?);
    }
    let mut a = store(root)?;
    a.tasks.insert(
        d.label.clone(),
        Approval {
            fingerprint: launch.fingerprint.clone(),
            executable: launch.executable.clone(),
            args: launch.args.clone(),
            cwd: launch.cwd.clone(),
            timeout_seconds: timeout,
            output_bytes: limit,
        },
    );
    let config = render_definition(
        before.as_deref(),
        &d,
        indices.first().copied(),
        previous.as_ref(),
    )?;
    let rendered: Value =
        jsonc_parser::parse_to_serde_value(std::str::from_utf8(&config)?, &jsonc_options())?;
    if rendered != v {
        return Err("task update could not be preserved safely".into());
    }
    let grants = serde_json::to_vec_pretty(&a)?;
    if config.len() as u64 > MAX || grants.len() as u64 > MAX {
        return Err("task file/store exceeds 128 KiB".into());
    }
    let parent = path.parent().unwrap();
    no_symlinks(parent)?;
    fs::create_dir_all(parent)?;
    no_symlinks(parent)?;
    if read_bounded(&path)? != before {
        return Err("tasks.json changed during registration; retry".into());
    }
    workspace.access.authorize(&path, true)?;
    save(&path, &config)?;
    save(&dir(root).join("approvals.json"), &grants)?;
    Ok(
        json!({"label":d.label,"authorized":true,"fingerprint":launch.fingerprint,"resolved_executable":launch.executable,"resolved_cwd":launch.cwd,"timeout_seconds":timeout,"output_bytes":limit,"persistent":true,"journaled":false}),
    )
}
// Anonymous protected files have no directory entries and are reclaimed by the
// OS on normal exit or crash. The current job is the only retained job.
const LOG_LIMIT: usize = 4 * 1024 * 1024;
#[cfg(unix)]
fn write_at(file: &File, bytes: &[u8], offset: u64) -> std::io::Result<usize> {
    file.write_at(bytes, offset)
}
#[cfg(windows)]
fn write_at(file: &File, bytes: &[u8], offset: u64) -> std::io::Result<usize> {
    file.seek_write(bytes, offset)
}
#[cfg(unix)]
fn read_at(file: &File, bytes: &mut [u8], offset: u64) -> std::io::Result<usize> {
    file.read_at(bytes, offset)
}
#[cfg(windows)]
fn read_at(file: &File, bytes: &mut [u8], offset: u64) -> std::io::Result<usize> {
    file.seek_read(bytes, offset)
}
struct LogProgress {
    lengths: [usize; 2],
    dropped: [u64; 2],
    done: bool,
    drained: bool,
}
struct Logs {
    files: [File; 2],
    progress: Mutex<LogProgress>,
    changed: Condvar,
}
impl Logs {
    fn create(root: &Path) -> Result<Self> {
        let dir = root.join(".ffsake/task-logs");
        no_symlinks(&dir)?;
        fs::create_dir_all(&dir)?;
        no_symlinks(&dir)?;
        Ok(Self {
            files: [tempfile::tempfile_in(&dir)?, tempfile::tempfile_in(&dir)?],
            progress: Mutex::new(LogProgress {
                lengths: [0, 0],
                dropped: [0, 0],
                done: false,
                drained: false,
            }),
            changed: Condvar::new(),
        })
    }
    fn append(&self, which: usize, bytes: &[u8]) -> Result<()> {
        let mut state = self.progress.lock().map_err(|_| "task log lock poisoned")?;
        let total = state.lengths[which]
            .checked_add(bytes.len())
            .ok_or("task output counter overflow")?;
        let mut written = 0;
        while written < bytes.len() {
            let offset = (state.lengths[which] + written) % LOG_LIMIT;
            let end = (bytes.len() - written).min(LOG_LIMIT - offset);
            let n = write_at(
                &self.files[which],
                &bytes[written..written + end],
                offset as u64,
            )?;
            if n == 0 {
                return Err("cannot write task log".into());
            }
            written += n;
        }
        state.lengths[which] = total;
        state.dropped[which] = total.saturating_sub(LOG_LIMIT) as u64;
        self.changed.notify_all();
        Ok(())
    }
    fn finish(&self, drained: bool) {
        if let Ok(mut state) = self.progress.lock() {
            state.done = true;
            state.drained = drained;
            self.changed.notify_all();
        }
    }
}
fn base64(bytes: &[u8]) -> String {
    const ALPHABET: &[u8; 64] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    let mut s = String::with_capacity(bytes.len().div_ceil(3) * 4);
    for c in bytes.chunks(3) {
        let a = c[0];
        let b = *c.get(1).unwrap_or(&0);
        let d = *c.get(2).unwrap_or(&0);
        s.push(ALPHABET[(a >> 2) as usize] as char);
        s.push(ALPHABET[(((a & 3) << 4) | (b >> 4)) as usize] as char);
        s.push(if c.len() > 1 {
            ALPHABET[(((b & 15) << 2) | (d >> 6)) as usize] as char
        } else {
            '='
        });
        s.push(if c.len() > 2 {
            ALPHABET[(d & 63) as usize] as char
        } else {
            '='
        });
    }
    s
}
struct Job {
    id: u64,
    cancel: Arc<AtomicBool>,
    state: Arc<Mutex<Value>>,
    logs: Arc<Logs>,
    worker: Option<JoinHandle<()>>,
}
impl Drop for Job {
    fn drop(&mut self) {
        self.cancel.store(true, Ordering::SeqCst);
        if let Some(w) = self.worker.take() {
            let _ = w.join();
        }
    }
}
#[derive(Default)]
pub(crate) struct Jobs {
    next: u64,
    job: Option<Job>,
}
impl Jobs {
    pub(crate) fn start(&mut self, workspace: &Workspace, label: &str) -> Result<Value> {
        if self
            .job
            .as_ref()
            .is_some_and(|j| j.state.lock().unwrap()["status"] == "running")
        {
            return Err("one project task may run at a time".into());
        }
        let root = workspace.root();
        workspace.access.authorize(&file_path(root), false)?;
        let (v, _) = task_file(root)?;
        let d = find(&v, label)?;
        let record = store(root)?
            .tasks
            .get(label)
            .cloned()
            .ok_or("task is not authorized; request host approval to register it")?;
        let launch = resolve(
            workspace,
            &d,
            record.timeout_seconds,
            record.output_bytes,
            Some(&record),
        )?;
        if launch.fingerprint != record.fingerprint {
            return Err("task definition, resolved executable, cwd or approved limits changed; host must reauthorize".into());
        }
        self.next += 1;
        let id = self.next;
        let state = Arc::new(Mutex::new(
            json!({"job":id,"label":label,"status":"running"}),
        ));
        let cancel = Arc::new(AtomicBool::new(false));
        let logs = Arc::new(Logs::create(root)?);
        // Spawn before returning the job ID: later edits cannot change which
        // executable was launched after the approved fingerprint was checked.
        let mut cmd = Command::new(&launch.executable);
        cmd.args(&launch.args).current_dir(&launch.cwd);
        let started = Instant::now();
        let child = workflow_process::spawn(&mut cmd)?;
        // Preserve the previous completed log if validation or spawn fails.
        self.job.take();
        let st = state.clone();
        let stop = cancel.clone();
        let name = label.to_owned();
        let log_writer = logs.clone();
        let worker = std::thread::spawn(move || {
            let out = workflow_process::capture_with(
                child,
                started,
                Duration::from_secs(record.timeout_seconds),
                record.output_bytes,
                Some(&stop),
                &mut |which, bytes| log_writer.append(which, bytes),
            );
            let drained = out.as_ref().is_ok_and(|o| o.drained);
            let value = match out {
                Ok(o) => {
                    #[cfg(unix)]
                    let signal = {
                        use std::os::unix::process::ExitStatusExt;
                        o.status.signal()
                    };
                    #[cfg(not(unix))]
                    let signal: Option<i32> = None;
                    json!({"job":id,"label":name,"status":"completed","success":o.status.success()&&!o.timed_out&&!o.cancelled,"exit_code":o.status.code(),"signal":signal,"timed_out":o.timed_out,"cancelled":o.cancelled,"output_truncated":o.truncated,"output_drained":o.drained,"stdout":String::from_utf8_lossy(&o.stdout),"stderr":String::from_utf8_lossy(&o.stderr),"elapsed_ms":o.elapsed_ms,"journaled":false,"sandboxed":false,"output_is_untrusted":true})
                }
                Err(e) => {
                    json!({"job":id,"label":name,"status":"failed","error":e.to_string(),"journaled":false})
                }
            };
            *st.lock().unwrap() = value;
            log_writer.finish(drained);
        });
        self.job = Some(Job {
            id,
            cancel,
            state,
            logs,
            worker: Some(worker),
        });
        Ok(json!({"job":id,"label":label,"status":"running","next":"poll ffsake_task_status"}))
    }
    pub(crate) fn status(&self, id: u64, include_output: bool) -> Result<Value> {
        let job = self
            .job
            .as_ref()
            .filter(|j| j.id == id)
            .ok_or("unknown or stale task job")?;
        let mut value = job.state.lock().unwrap().clone();
        if !include_output && let Some(object) = value.as_object_mut() {
            object.remove("stdout");
            object.remove("stderr");
            object.insert("output_included".into(), json!(false));
        }
        Ok(value)
    }
    pub(crate) fn output(
        &self,
        id: u64,
        stream: &str,
        cursor: usize,
        max_bytes: usize,
        wait_ms: u64,
    ) -> Result<Value> {
        if !(4..=65536).contains(&max_bytes) || wait_ms > 1000 {
            return Err("max_bytes must be 4..65536 and wait_ms must be <=1000".into());
        }
        let which = match stream {
            "stdout" => 0,
            "stderr" => 1,
            _ => return Err("stream must be stdout or stderr".into()),
        };
        let job = self
            .job
            .as_ref()
            .filter(|j| j.id == id)
            .ok_or("unknown or expired task log")?;
        let logs = &job.logs;
        let mut state = logs.progress.lock().map_err(|_| "task log lock poisoned")?;
        let deadline = Instant::now() + Duration::from_millis(wait_ms);
        let (bytes, first) = loop {
            if cursor > state.lengths[which] {
                return Err("cursor exceeds retained log length".into());
            }
            let first = state.lengths[which].saturating_sub(LOG_LIMIT);
            if cursor < first {
                return Ok(
                    json!({"job":id,"stream":stream,"cursor":cursor,"next_cursor":first,"cursor_expired":true,"first_cursor":first,"retained_bytes":state.lengths[which]-first,"dropped_bytes":state.dropped[which],"complete":state.done,"drained":state.done && state.drained,"log_complete":false,"text":"","data_base64":""}),
                );
            }
            let mut bytes = vec![0; (state.lengths[which] - cursor).min(max_bytes)];
            let mut read = 0;
            while read < bytes.len() {
                let offset = (cursor + read) % LOG_LIMIT;
                let amount = (bytes.len() - read).min(LOG_LIMIT - offset);
                let n = read_at(
                    &logs.files[which],
                    &mut bytes[read..read + amount],
                    offset as u64,
                )?;
                if n == 0 {
                    return Err("task log unexpectedly truncated".into());
                }
                read += n;
            }
            if let Err(e) = std::str::from_utf8(&bytes)
                && e.error_len().is_none()
                && (!state.done || cursor + bytes.len() < state.lengths[which])
            {
                bytes.truncate(e.valid_up_to());
            }
            let remaining = deadline.saturating_duration_since(Instant::now());
            if !bytes.is_empty() || state.done || remaining.is_zero() {
                break (bytes, first);
            }
            let previous = state.lengths[which];
            state = logs
                .changed
                .wait_timeout_while(state, remaining, |s| {
                    previous == s.lengths[which] && !s.done
                })
                .map_err(|_| "task log lock poisoned")?
                .0;
        };
        let text = String::from_utf8_lossy(&bytes);
        let valid = std::str::from_utf8(&bytes).is_ok();
        Ok(
            json!({"job":id,"stream":stream,"cursor":cursor,"next_cursor":cursor+bytes.len(),"first_cursor":first,"cursor_expired":false,"text":text,"text_is_lossy":!valid,"data_base64":base64(&bytes),"retained_bytes":state.lengths[which]-first,"total_bytes":state.lengths[which],"retention_limit_bytes":LOG_LIMIT,"dropped_bytes":state.dropped[which],"complete":state.done,"drained":state.done && state.drained,"log_complete":state.done && state.drained && state.dropped[which]==0,"output_is_untrusted":true}),
        )
    }
    pub(crate) fn cancel(&self, id: u64) -> Result<Value> {
        let job = self
            .job
            .as_ref()
            .filter(|j| j.id == id)
            .ok_or("unknown or stale task job")?;
        if job.state.lock().unwrap()["status"] != "running" {
            return Err("task already completed; nothing to cancel".into());
        }
        job.cancel.store(true, Ordering::SeqCst);
        Ok(
            json!({"job":id,"status":"cancelling","next":"poll ffsake_task_status; the task may finish before cancellation takes effect"}),
        )
    }
}
