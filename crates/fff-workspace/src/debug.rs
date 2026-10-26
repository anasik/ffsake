//! Host-configured local DAP sessions. No adapter-specific code or implicit execution.
use crate::{access::Access, dap};
use serde::Deserialize;
use serde_json::{Value, json};
use std::{
    collections::BTreeMap,
    fs,
    io::Read,
    path::{Path, PathBuf},
    sync::{
        Arc, Mutex,
        atomic::{AtomicBool, Ordering},
        mpsc,
    },
    thread::JoinHandle,
};
pub(crate) type Result<T> = std::result::Result<T, Box<dyn std::error::Error>>;
#[derive(Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Configuration {
    pub(crate) argv: Vec<String>,
    pub(crate) launch: Value,
    #[serde(default = "timeout")]
    pub(crate) timeout_seconds: u64,
    #[serde(default = "lifetime")]
    pub(crate) session_seconds: u64,
}
fn timeout() -> u64 {
    10
}
fn lifetime() -> u64 {
    300
}
#[derive(Default, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Configurations {
    version: u32,
    configurations: BTreeMap<String, Configuration>,
}
impl Configurations {
    pub fn load(path: &Path) -> Result<Self> {
        let metadata = fs::metadata(path)?;
        if !metadata.is_file() || metadata.len() > 65536 {
            return Err("debug configuration must be a regular JSON file <=64 KiB".into());
        }
        let mut data = Vec::new();
        fs::File::open(path)?.take(65537).read_to_end(&mut data)?;
        if data.len() > 65536 {
            return Err("debug configuration exceeds 64 KiB".into());
        }
        let value: Self = serde_json::from_slice(&data)?;
        if value.version != 1 || value.configurations.is_empty() || value.configurations.len() > 16
        {
            return Err("debug configuration requires version 1 and 1..16 entries".into());
        }
        for (name, c) in &value.configurations {
            if name.is_empty()
                || name.len() > 64
                || c.argv.is_empty()
                || c.argv.len() > 32
                || !Path::new(&c.argv[0]).is_absolute()
                || c.argv.iter().any(|s| s.len() > 4096 || s.contains('\0'))
                || !c.launch.is_object()
                || !(1..=30).contains(&c.timeout_seconds)
                || !(5..=600).contains(&c.session_seconds)
            {
                return Err("invalid debug configuration: absolute adapter executable, bounded argv/launch/time required".into());
            }
        }
        Ok(value)
    }
}
#[derive(Debug, Deserialize)]
#[serde(tag = "action", rename_all = "snake_case", deny_unknown_fields)]
pub enum Action {
    List,
    Poll {
        session: u64,
    },
    Terminate {
        session: u64,
    },
    Breakpoints {
        session: u64,
        path: String,
        lines: Vec<u32>,
    },
    Threads {
        session: u64,
    },
    Stack {
        session: u64,
        thread: i64,
        #[serde(default)]
        start: usize,
        #[serde(default = "limit")]
        limit: usize,
    },
    Scopes {
        session: u64,
        handle: u64,
    },
    Variables {
        session: u64,
        handle: u64,
        #[serde(default)]
        start: usize,
        #[serde(default = "limit")]
        limit: usize,
    },
    Source {
        session: u64,
        handle: u64,
    },
    Continue {
        session: u64,
        thread: Option<i64>,
    },
    Next {
        session: u64,
        thread: i64,
    },
    StepIn {
        session: u64,
        thread: i64,
    },
    StepOut {
        session: u64,
        thread: i64,
    },
    Pause {
        session: u64,
        thread: i64,
    },
}
fn limit() -> usize {
    20
}
impl Action {
    fn session(&self) -> Option<u64> {
        match self {
            Self::List => None,
            Self::Poll { session }
            | Self::Terminate { session }
            | Self::Breakpoints { session, .. }
            | Self::Threads { session }
            | Self::Stack { session, .. }
            | Self::Scopes { session, .. }
            | Self::Variables { session, .. }
            | Self::Source { session, .. }
            | Self::Continue { session, .. }
            | Self::Next { session, .. }
            | Self::StepIn { session, .. }
            | Self::StepOut { session, .. }
            | Self::Pause { session, .. } => Some(*session),
        }
    }
}
pub(crate) struct Reply {
    pub value: Value,
    pub paths: Vec<PathBuf>,
}
pub(crate) struct State {
    pub status: String,
    pub capabilities: Value,
    pub stop: Value,
    pub events: Vec<Value>,
    pub output: String,
    pub truncated: bool,
    pub busy: bool,
    pub result: Option<Reply>,
    pub error: Option<String>,
    pub adapter_pid: Option<u32>,
    pub cleaned: bool,
}
impl Default for State {
    fn default() -> Self {
        Self {
            status: "starting".into(),
            capabilities: json!({}),
            stop: Value::Null,
            events: Vec::new(),
            output: String::new(),
            truncated: false,
            busy: true,
            result: None,
            error: None,
            adapter_pid: None,
            cleaned: false,
        }
    }
}
struct Session {
    id: u64,
    state: Arc<Mutex<State>>,
    cancel: Arc<AtomicBool>,
    commands: mpsc::SyncSender<Action>,
    worker: Option<JoinHandle<()>>,
}
impl Drop for Session {
    fn drop(&mut self) {
        self.cancel.store(true, Ordering::SeqCst);
        if let Some(worker) = self.worker.take() {
            let _ = worker.join();
        }
    }
}
#[derive(Default)]
pub struct Debugger {
    configurations: Configurations,
    session: Option<Session>,
    next: u64,
}
impl Debugger {
    pub fn configure(&mut self, configurations: Configurations) {
        self.configurations = configurations;
    }
    pub fn launch(&mut self, name: &str, root: &Path, access: Access) -> Result<Value> {
        if self
            .session
            .as_ref()
            .is_some_and(|s| !s.state.lock().unwrap().cleaned)
        {
            return Err("terminate the current debug session first".into());
        }
        let configuration = self
            .configurations
            .configurations
            .get(name)
            .ok_or("unknown debug configuration; host must configure --debug at startup")?
            .clone();
        self.session.take();
        self.next += 1;
        let state = Arc::new(Mutex::new(State::default()));
        let cancel = Arc::new(AtomicBool::new(false));
        let (commands, receive) = mpsc::sync_channel(1);
        let (s, c, r) = (state.clone(), cancel.clone(), root.to_owned());
        let worker = std::thread::spawn(move || dap::run(configuration, r, access, s, c, receive));
        self.session = Some(Session {
            id: self.next,
            state,
            cancel,
            commands,
            worker: Some(worker),
        });
        Ok(
            json!({"session":self.next,"status":"starting","next":"poll until configuring, set breakpoints, then continue to finish configuration","executes_project_code":true,"filesystem_sandbox":false}),
        )
    }
    pub fn execute(&mut self, action: Action, access: &Access) -> Result<Value> {
        if matches!(action, Action::List) {
            return Ok(
                json!({"configurations":self.configurations.configurations.keys().collect::<Vec<_>>(),"execution_enabled":!self.configurations.configurations.is_empty(),"transport":"local stdio DAP","one_session":true}),
            );
        }
        let session = self
            .session
            .as_mut()
            .filter(|s| Some(s.id) == action.session())
            .ok_or("unknown or stale debug session")?;
        if matches!(action, Action::Terminate { .. }) {
            session.cancel.store(true, Ordering::SeqCst);
            return Ok(
                json!({"session":session.id,"status":"terminating","next":"poll until cleaned=true"}),
            );
        }
        let mut state = session.state.lock().unwrap();
        if matches!(action, Action::Poll { .. }) {
            let result = state.result.take().map(|reply| {
                let checked = (|| -> Result<()> {
                    access.refresh()?;
                    for path in &reply.paths {
                        access.authorize(path, false)?;
                    }
                    Ok(())
                })();
                match checked {
                    Ok(()) => reply.value,
                    Err(e) => json!({"ok":false,"error":e.to_string()}),
                }
            });
            return Ok(
                json!({"session":session.id,"status":state.status,"capabilities":state.capabilities,"stop":state.stop,"busy":state.busy,"result":result,"events":std::mem::take(&mut state.events),"output":std::mem::take(&mut state.output),"output_truncated":state.truncated,"error":state.error,"adapter_pid":state.adapter_pid,"cleaned":state.cleaned}),
            );
        }
        if state.cleaned {
            return Err("debug session ended".into());
        }
        if state.busy || state.result.is_some() {
            return Err(
                "poll and consume the previous debug result before another operation".into(),
            );
        }
        session.commands.try_send(action)?;
        state.busy = true;
        Ok(
            json!({"session":session.id,"queued":true,"next":"poll; ordinary editing remains available"}),
        )
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn fixed_launch_configuration_rejects_unbounded_or_ambiguous_inputs() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("debug.json");
        let valid = json!({"version":1,"configurations":{"one":{"argv":["/adapter","--stdio"],"launch":{"program":"${workspaceFolder}/app"}},"two":{"argv":["/other-adapter"],"launch":{}}}});
        fs::write(&path, valid.to_string()).unwrap();
        assert_eq!(Configurations::load(&path).unwrap().configurations.len(), 2);
        for (field, value) in [
            ("argv", json!(["relative-adapter"])),
            ("argv", json!(["/adapter", "\0"])),
            ("launch", json!([])),
            ("timeout_seconds", json!(0)),
            ("session_seconds", json!(601)),
            ("unknown", json!(true)),
        ] {
            let mut invalid = valid.clone();
            invalid["configurations"]["one"][field] = value;
            fs::write(&path, invalid.to_string()).unwrap();
            assert!(Configurations::load(&path).is_err(), "{field}");
        }
        fs::write(&path, vec![b' '; 65537]).unwrap();
        assert!(Configurations::load(&path).is_err());
    }

    #[test]
    fn debug_actions_cannot_supply_execution_or_evaluation_arguments() {
        for value in [
            json!({"action":"evaluate","expression":"run()","session":1}),
            json!({"action":"attach","pid":1,"session":1}),
            json!({"action":"continue","session":1,"argv":["/bin/sh"]}),
            json!({"action":"variables","session":1,"handle":1,"value":"changed"}),
        ] {
            assert!(serde_json::from_value::<Action>(value).is_err());
        }
    }
}
