//! Minimal standard DAP client. One background owner; no language-specific commands.
use crate::{
    access::Access,
    debug::{Action, Configuration, Reply, State},
    lsp_transport::read_frame,
    workflow_process::Guard,
};
use serde_json::{Value, json};
use std::{
    collections::BTreeMap,
    io::{BufReader, Read, Write},
    path::{Path, PathBuf},
    process::{Command, Stdio},
    sync::{
        Arc, Mutex,
        atomic::{AtomicBool, Ordering},
        mpsc::{self, Receiver, SyncSender},
    },
    time::{Duration, Instant},
};
type Result<T> = std::result::Result<T, String>;
type Outgoing = (Value, SyncSender<std::io::Result<()>>);
#[derive(Clone)]
enum Handle {
    Frame(i64, PathBuf),
    Variables(i64, PathBuf, usize),
    Source(i64, PathBuf),
}
struct Client {
    child: Guard,
    incoming: Receiver<Result<Value>>,
    outgoing: SyncSender<Outgoing>,
    state: Arc<Mutex<State>>,
    cancel: Arc<AtomicBool>,
    access: Access,
    next: i64,
    responses: BTreeMap<i64, Value>,
    pending: BTreeMap<i64, String>,
    launch: Option<i64>,
    initialized: bool,
    configured: bool,
    stopped: bool,
    ended: bool,
    fatal: bool,
    handles: BTreeMap<u64, Handle>,
    handle_next: u64,
    epoch: u64,
    timeout: Duration,
    expires: Instant,
}
fn short(s: &str, max: usize) -> String {
    let mut end = s.len().min(max);
    while !s.is_char_boundary(end) {
        end -= 1;
    }
    s[..end].to_owned()
}
fn output(state: &Arc<Mutex<State>>, s: &str) {
    let mut st = state.lock().unwrap();
    let remaining = 65536usize.saturating_sub(st.output.len());
    st.truncated |= s.len() > remaining;
    st.output.push_str(&short(s, remaining));
}
impl Client {
    fn spawn(
        c: &Configuration,
        root: PathBuf,
        access: Access,
        state: Arc<Mutex<State>>,
        cancel: Arc<AtomicBool>,
    ) -> Result<Self> {
        #[cfg(not(unix))]
        return Err("DAP requires Unix process-group cleanup in this milestone".into());
        #[cfg(unix)]
        {
            use std::os::unix::process::CommandExt;
            let mut command = Command::new(&c.argv[0]);
            command
                .args(&c.argv[1..])
                .current_dir(&root)
                .process_group(0)
                .stdin(Stdio::piped())
                .stdout(Stdio::piped())
                .stderr(Stdio::piped());
            let mut child = Guard(
                command
                    .spawn()
                    .map_err(|e| format!("cannot launch configured debug adapter: {e}"))?,
            );
            state.lock().unwrap().adapter_pid = Some(child.0.id());
            let mut stdin = child.0.stdin.take().ok_or("adapter stdin unavailable")?;
            let stdout = child.0.stdout.take().ok_or("adapter stdout unavailable")?;
            let mut stderr = child.0.stderr.take().ok_or("adapter stderr unavailable")?;
            let (outgoing, write) = mpsc::sync_channel::<Outgoing>(1);
            std::thread::spawn(move || {
                while let Ok((value, reply)) = write.recv() {
                    let data = serde_json::to_vec(&value).unwrap();
                    let result = write!(stdin, "Content-Length: {}\r\n\r\n", data.len())
                        .and_then(|_| stdin.write_all(&data))
                        .and_then(|_| stdin.flush());
                    let failed = result.is_err();
                    let _ = reply.send(result);
                    if failed {
                        break;
                    }
                }
            });
            let (send, incoming) = mpsc::sync_channel(8);
            std::thread::spawn(move || {
                let mut reader = BufReader::new(stdout);
                loop {
                    let result = read_frame(&mut reader, &mut 0)
                        .map_err(|e| format!("DAP frame/connection error: {e}"));
                    let failed = result.is_err();
                    if send.send(result).is_err() || failed {
                        break;
                    }
                }
            });
            let out = state.clone();
            std::thread::spawn(move || {
                let mut bytes = [0; 4096];
                while let Ok(n) = stderr.read(&mut bytes) {
                    if n == 0 {
                        break;
                    }
                    output(&out, &String::from_utf8_lossy(&bytes[..n]));
                }
            });
            Ok(Self {
                child,
                incoming,
                outgoing,
                state,
                cancel,
                access,
                next: 1,
                responses: BTreeMap::new(),
                pending: BTreeMap::new(),
                launch: None,
                initialized: false,
                configured: false,
                stopped: false,
                ended: false,
                fatal: false,
                handles: BTreeMap::new(),
                handle_next: 1,
                epoch: 0,
                timeout: Duration::from_secs(c.timeout_seconds),
                expires: Instant::now() + Duration::from_secs(c.session_seconds),
            })
        }
    }
    fn live(&mut self, deadline: Instant) -> Result<()> {
        if self.cancel.load(Ordering::SeqCst) {
            return Err("debug session cancelled".into());
        }
        if Instant::now() >= deadline || Instant::now() >= self.expires {
            self.fatal = true;
            return Err(
                "debug adapter request/session timed out; terminating owned processes".into(),
            );
        }
        Ok(())
    }
    fn write(&mut self, value: Value) -> Result<()> {
        let (send, recv) = mpsc::sync_channel(1);
        self.outgoing.try_send((value, send)).map_err(|_| {
            self.fatal = true;
            "DAP write queue unavailable".to_string()
        })?;
        let deadline = Instant::now() + self.timeout;
        loop {
            self.live(deadline)?;
            match recv.recv_timeout(Duration::from_millis(10)) {
                Ok(r) => {
                    return r.map_err(|e| {
                        self.fatal = true;
                        e.to_string()
                    });
                }
                Err(mpsc::RecvTimeoutError::Timeout) => {}
                Err(_) => {
                    self.fatal = true;
                    return Err("DAP writer disconnected".into());
                }
            }
        }
    }
    fn send(&mut self, command: &str, arguments: Value) -> Result<i64> {
        let seq = self.next;
        self.next += 1;
        self.pending.insert(seq, command.into());
        self.write(json!({"seq":seq,"type":"request","command":command,"arguments":arguments}))?;
        Ok(seq)
    }
    fn response(&mut self, id: i64) -> Result<Option<Value>> {
        if let Some(value) = self.responses.remove(&id) {
            if value["success"] != true {
                return Err(format!(
                    "adapter rejected {}: {}",
                    value["command"].as_str().unwrap_or("request"),
                    short(
                        value["message"]
                            .as_str()
                            .unwrap_or("unsupported or failed operation"),
                        1024
                    )
                ));
            }
            return Ok(Some(value.get("body").cloned().unwrap_or(json!({}))));
        }
        Ok(None)
    }
    fn request(&mut self, command: &str, arguments: Value) -> Result<Value> {
        let id = self.send(command, arguments)?;
        let deadline = Instant::now() + self.timeout;
        loop {
            if let Some(result) = self.response(id)? {
                return Ok(result);
            }
            self.live(deadline)?;
            self.receive(Duration::from_millis(10))?;
        }
    }
    fn invalidate(&mut self) {
        self.epoch += 1;
        self.handles.clear();
        self.stopped = false;
        self.state.lock().unwrap().stop = Value::Null;
    }
    fn receive(&mut self, wait: Duration) -> Result<()> {
        let message = match self.incoming.recv_timeout(wait) {
            Ok(Ok(v)) => v,
            Ok(Err(e)) => {
                self.fatal = true;
                return Err(e);
            }
            Err(mpsc::RecvTimeoutError::Timeout) => return Ok(()),
            Err(_) => {
                self.fatal = true;
                return Err("debug adapter disconnected".into());
            }
        };
        match message["type"].as_str() {
            Some("response") => {
                let id = message["request_seq"]
                    .as_i64()
                    .ok_or("invalid DAP request_seq")?;
                let command = self.pending.remove(&id).ok_or("unexpected DAP response")?;
                if message["command"] != command || !message["success"].is_boolean() {
                    self.fatal = true;
                    return Err("invalid DAP response correlation".into());
                }
                self.responses.insert(id, message);
                if self.responses.len() > 8 {
                    self.fatal = true;
                    return Err("too many DAP responses".into());
                }
            }
            Some("request") => {
                let seq = self.next;
                self.next += 1;
                self.write(json!({"seq":seq,"type":"response","request_seq":message["seq"],"command":message["command"],"success":false,"message":"FFSAKE does not support reverse requests, terminals or child debug sessions"}))?;
            }
            Some("event") => {
                let event = message["event"].as_str().ok_or("invalid DAP event")?;
                let body = &message["body"];
                match event {
                    "initialized" => self.initialized = true,
                    "stopped" => {
                        self.invalidate();
                        self.stopped = true;
                        let mut s = self.state.lock().unwrap();
                        s.status = "stopped".into();
                        s.stop = json!({"reason":short(body["reason"].as_str().unwrap_or("unknown"),256),"description":short(body["description"].as_str().unwrap_or(""),1024),"threadId":body["threadId"].as_i64(),"allThreadsStopped":body["allThreadsStopped"].as_bool()});
                    }
                    "continued" => {
                        self.invalidate();
                        self.state.lock().unwrap().status = "running".into();
                    }
                    "terminated" => {
                        self.invalidate();
                        self.ended = true;
                        self.state.lock().unwrap().status = "terminated".into();
                    }
                    "exited" => {
                        self.invalidate();
                    }
                    "invalidated" => {
                        self.epoch += 1;
                        self.handles.clear();
                    }
                    "capabilities" => {
                        if let Some(c) = body["capabilities"].as_object() {
                            let mut s = self.state.lock().unwrap();
                            for (k, v) in c {
                                if v.is_boolean()
                                    && k.len() <= 128
                                    && (s.capabilities.get(k).is_some()
                                        || s.capabilities.as_object().unwrap().len() < 128)
                                {
                                    s.capabilities[k] = v.clone();
                                }
                            }
                        }
                    }
                    "output" => output(&self.state, body["output"].as_str().unwrap_or("")),
                    _ => {}
                }
                if event != "output" {
                    let mut s = self.state.lock().unwrap();
                    if s.events.len() == 32 {
                        s.events.remove(0);
                        s.truncated = true;
                    }
                    s.events.push(json!({"event":short(event,128),"threadId":body["threadId"].as_i64(),"exitCode":body["exitCode"].as_i64(),"processId":body["systemProcessId"].as_u64()}));
                }
            }
            _ => {
                self.fatal = true;
                return Err("invalid DAP message type".into());
            }
        }
        Ok(())
    }
    fn capability(&self, name: &str) -> bool {
        self.state.lock().unwrap().capabilities[name] == true
    }
    fn handle(&mut self, value: Handle) -> Result<u64> {
        if self.handles.len() >= 512 {
            return Err(
                "debug handle limit reached; resume or restart before further expansion".into(),
            );
        }
        let id = self.handle_next;
        self.handle_next += 1;
        self.handles.insert(id, value);
        Ok(id)
    }
    fn get(&self, id: u64) -> Result<Handle> {
        if !self.stopped {
            return Err("program is not stopped; frame/variable handles are stale".into());
        }
        self.handles
            .get(&id)
            .cloned()
            .ok_or("unknown or stale debug handle".into())
    }
    fn authorize(&self, path: &Path) -> Result<PathBuf> {
        self.access.refresh().map_err(|e| e.to_string())?;
        self.access
            .authorize(path, false)
            .map(|(p, _)| p)
            .map_err(|e| e.to_string())
    }
    fn stopped(&self) -> Result<()> {
        if self.stopped {
            Ok(())
        } else {
            Err("operation requires a stopped program".into())
        }
    }
    fn perform(&mut self, action: Action) -> Result<Reply> {
        let epoch = self.epoch;
        let inspection = matches!(
            &action,
            Action::Stack { .. }
                | Action::Scopes { .. }
                | Action::Variables { .. }
                | Action::Source { .. }
        );
        let mut paths = Vec::new();
        let value = match action {
            Action::Breakpoints { path, lines, .. } => {
                if lines.len() > 64 || lines.contains(&0) {
                    return Err("breakpoints require <=64 positive source lines".into());
                }
                let path = self.authorize(Path::new(&path))?;
                paths.push(path.clone());
                self.request("setBreakpoints",json!({"source":{"path":path},"breakpoints":lines.iter().map(|line|json!({"line":line})).collect::<Vec<_>>(),"sourceModified":false}))?
            }
            Action::Threads { .. } => {
                let value = self.request("threads", json!({}))?;
                let entries = value["threads"]
                    .as_array()
                    .ok_or("invalid threads response")?;
                json!({"threads":entries.iter().take(100).map(|t|json!({"id":t["id"],"name":short(t["name"].as_str().unwrap_or(""),512)})).collect::<Vec<_>>(),"truncated":entries.len()>100})
            }
            Action::Stack {
                thread,
                start,
                limit,
                ..
            } => {
                self.stopped()?;
                bounds(start, limit)?;
                let result = self.request(
                    "stackTrace",
                    json!({"threadId":thread,"startFrame":start,"levels":limit}),
                )?;
                let frames = result["stackFrames"]
                    .as_array()
                    .ok_or("invalid stack response")?;
                let mut out = Vec::new();
                for frame in frames.iter().take(limit) {
                    let source = &frame["source"];
                    let mut entry = json!({"name":short(frame["name"].as_str().unwrap_or(""),1024),"line":frame["line"],"column":frame["column"],"source_path":source["path"],"source_authorized":false});
                    if let Some(path) = source["path"].as_str()
                        && let Ok(path) = self.authorize(Path::new(path))
                    {
                        paths.push(path.clone());
                        entry["source_authorized"] = json!(true);
                        entry["handle"] = json!(self.handle(Handle::Frame(
                            frame["id"].as_i64().ok_or("invalid frame id")?,
                            path.clone()
                        ))?);
                        entry["source_handle"] = json!(self.handle(Handle::Source(
                            source["sourceReference"].as_i64().unwrap_or(0),
                            path
                        ))?);
                    }
                    out.push(entry);
                }
                json!({"frames":out,"totalFrames":result["totalFrames"],"truncated":frames.len()>limit})
            }
            Action::Scopes { handle, .. } => {
                let Handle::Frame(id, path) = self.get(handle)? else {
                    return Err("expected frame handle".into());
                };
                self.authorize(&path)?;
                paths.push(path.clone());
                let result = self.request("scopes", json!({"frameId":id}))?;
                let scopes = result["scopes"]
                    .as_array()
                    .ok_or("invalid scopes response")?;
                let mut out = Vec::new();
                for scope in scopes.iter().take(32) {
                    let id = scope["variablesReference"].as_i64().unwrap_or(0);
                    out.push(json!({"name":short(scope["name"].as_str().unwrap_or(""),512),"expensive":scope["expensive"],"handle":if id>0{Some(self.handle(Handle::Variables(id,path.clone(),0))?)}else{None}}));
                }
                json!({"scopes":out,"truncated":scopes.len()>32})
            }
            Action::Variables {
                handle,
                start,
                limit,
                ..
            } => {
                bounds(start, limit)?;
                let Handle::Variables(id, path, depth) = self.get(handle)? else {
                    return Err("expected variables handle".into());
                };
                self.authorize(&path)?;
                paths.push(path.clone());
                if depth >= 4 {
                    return Err("variable expansion depth limit is 4".into());
                }
                let result = self.request(
                    "variables",
                    json!({"variablesReference":id,"start":start,"count":limit}),
                )?;
                let vars = result["variables"]
                    .as_array()
                    .ok_or("invalid variables response")?;
                let mut out = Vec::new();
                for var in vars.iter().take(limit) {
                    let id = var["variablesReference"].as_i64().unwrap_or(0);
                    out.push(json!({"name":short(var["name"].as_str().unwrap_or(""),512),"value":short(var["value"].as_str().unwrap_or(""),2048),"type":short(var["type"].as_str().unwrap_or(""),256),"handle":if id>0{Some(self.handle(Handle::Variables(id,path.clone(),depth+1))?)}else{None}}));
                }
                json!({"variables":out,"truncated":vars.len()>limit,"paging":"adapter-dependent; response and expansion are bounded"})
            }
            Action::Source { handle, .. } => {
                let Handle::Source(id, path) = self.get(handle)? else {
                    return Err("expected source handle".into());
                };
                self.authorize(&path)?;
                paths.push(path.clone());
                if id <= 0 {
                    return Err(
                        "adapter sourceReference is zero; use ordinary ffsake_read on source_path"
                            .into(),
                    );
                }
                let result = self.request(
                    "source",
                    json!({"source":{"path":path,"sourceReference":id},"sourceReference":id}),
                )?;
                let content = result["content"]
                    .as_str()
                    .ok_or("invalid source response")?;
                if content.len() > 65536 {
                    return Err("adapter source exceeds 64 KiB; use a bounded file read".into());
                }
                json!({"content":content})
            }
            Action::Continue { thread, .. } if !self.configured => {
                if !self.capability("supportsConfigurationDoneRequest") {
                    return Err(
                        "adapter does not support configurationDone; this milestone requires it"
                            .into(),
                    );
                }
                let _ = thread;
                self.configured = true;
                self.invalidate();
                self.state.lock().unwrap().status = "running".into();
                self.request("configurationDone", json!({}))?
            }
            Action::Continue { thread, .. } => {
                self.stopped()?;
                let thread = thread.ok_or("continue requires thread after configuration")?;
                self.invalidate();
                self.state.lock().unwrap().status = "running".into();
                self.request("continue", json!({"threadId":thread,"singleThread":false}))?
            }
            Action::Next { thread, .. }
            | Action::StepIn { thread, .. }
            | Action::StepOut { thread, .. } => {
                self.stopped()?;
                let command = match action {
                    Action::Next { .. } => "next",
                    Action::StepIn { .. } => "stepIn",
                    _ => "stepOut",
                };
                self.invalidate();
                self.state.lock().unwrap().status = "running".into();
                self.request(command, json!({"threadId":thread,"singleThread":false}))?
            }
            Action::Pause { thread, .. } => self.request("pause", json!({"threadId":thread}))?,
            _ => return Err("unsupported worker operation".into()),
        };
        if inspection && (!self.stopped || epoch != self.epoch) {
            self.handles.clear();
            return Err(
                "execution changed during inspection; request fresh stopped-state handles".into(),
            );
        }
        for path in &paths {
            self.authorize(path)?;
        }
        if serde_json::to_vec(&value).map_err(|e| e.to_string())?.len() > 65536 {
            return Err("debug response exceeds 64 KiB; narrow the request".into());
        }
        Ok(Reply {
            value: json!({"ok":true,"result":value}),
            paths,
        })
    }
    fn cleanup(&mut self) {
        // Best effort standard disconnect first, then kill the owned process group.
        let seq = self.next;
        self.next += 1;
        let (send, _) = mpsc::sync_channel(1);
        let _=self.outgoing.try_send((json!({"seq":seq,"type":"request","command":"disconnect","arguments":{"terminateDebuggee":true}}),send));
        let until = Instant::now() + Duration::from_millis(250);
        while Instant::now() < until {
            if self.child.0.try_wait().ok().flatten().is_some() {
                break;
            }
            std::thread::sleep(Duration::from_millis(10));
        }
        self.child.terminate();
        let _ = self.child.0.wait();
        self.handles.clear();
    }
}
fn boolean_capabilities(value: &Value) -> Value {
    let mut out = serde_json::Map::new();
    if let Some(map) = value.as_object() {
        for (key, value) in map.iter().take(128) {
            if key.len() <= 128 && value.is_boolean() {
                out.insert(key.clone(), value.clone());
            }
        }
    }
    Value::Object(out)
}

fn bounds(start: usize, limit: usize) -> Result<()> {
    if start > 10000 || !(1..=100).contains(&limit) {
        Err("start <=10000 and limit 1..100 required".into())
    } else {
        Ok(())
    }
}
fn expand(value: &mut Value, root: &Path) {
    match value {
        Value::String(s) => *s = s.replace("${workspaceFolder}", &root.to_string_lossy()),
        Value::Array(a) => {
            for v in a {
                expand(v, root)
            }
        }
        Value::Object(o) => {
            for v in o.values_mut() {
                expand(v, root)
            }
        }
        _ => {}
    }
}
pub(crate) fn run(
    mut config: Configuration,
    root: PathBuf,
    access: Access,
    state: Arc<Mutex<State>>,
    cancel: Arc<AtomicBool>,
    commands: Receiver<Action>,
) {
    let result = (|| -> Result<()> {
        let mut client =
            Client::spawn(&config, root.clone(), access, state.clone(), cancel.clone())?;
        let result = (|| -> Result<()> {
            let capabilities=client.request("initialize",json!({"clientID":"ffsake","clientName":"FFSAKE","adapterID":"configured","pathFormat":"path","linesStartAt1":true,"columnsStartAt1":true,"supportsVariableType":true,"supportsVariablePaging":true,"supportsRunInTerminalRequest":false,"supportsStartDebuggingRequest":false}))?;
            state.lock().unwrap().capabilities = boolean_capabilities(&capabilities);
            if !client.capability("supportsConfigurationDoneRequest") {
                return Err(
                    "adapter lacks configurationDone support required by this milestone".into(),
                );
            }
            expand(&mut config.launch, &root);
            if config.launch.get("cwd").is_none() {
                config.launch["cwd"] = json!(root);
            }
            client.launch = Some(client.send("launch", config.launch)?);
            let deadline = Instant::now() + client.timeout;
            while !client.initialized {
                client.live(deadline)?;
                client.receive(Duration::from_millis(10))?;
                if let Some(id) = client.launch
                    && client.response(id)?.is_some()
                {
                    client.launch = None;
                }
            }
            {
                let mut s = state.lock().unwrap();
                s.status = "configuring".into();
                s.busy = false;
            }
            loop {
                client.live(client.expires)?;
                client.receive(Duration::from_millis(5))?;
                if client.ended {
                    break;
                }
                if let Some(id) = client.launch
                    && client.response(id)?.is_some()
                {
                    client.launch = None;
                }
                match commands.try_recv() {
                    Ok(action) => {
                        let result = client.perform(action);
                        let mut s = state.lock().unwrap();
                        s.busy = false;
                        s.result = Some(match result {
                            Ok(reply) => reply,
                            Err(error) => Reply {
                                value: json!({"ok":false,"error":error}),
                                paths: vec![],
                            },
                        });
                        if client.fatal {
                            break;
                        }
                    }
                    Err(mpsc::TryRecvError::Disconnected) => break,
                    Err(mpsc::TryRecvError::Empty) => {}
                }
            }
            Ok(())
        })();
        client.cleanup();
        result
    })();
    let mut s = state.lock().unwrap();
    s.busy = false;
    s.cleaned = true;
    if let Err(error) = result {
        if cancel.load(Ordering::SeqCst) {
            s.status = "terminated".into();
        } else {
            s.status = "failed".into();
            s.error = Some(error);
        }
    } else {
        s.status = "terminated".into();
    }
}
