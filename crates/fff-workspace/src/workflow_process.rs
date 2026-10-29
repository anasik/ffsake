//! Bounded subprocess capture for trusted host jobs and fixed read-only Git commands.
use std::{
    io::Read,
    process::{Child, Command, ExitStatus, Stdio},
    sync::{
        atomic::{AtomicBool, Ordering},
        mpsc,
    },
    time::{Duration, Instant},
};
type Result<T> = std::result::Result<T, Box<dyn std::error::Error>>;
pub(crate) struct Output {
    pub status: ExitStatus,
    pub stdout: Vec<u8>,
    pub stderr: Vec<u8>,
    pub truncated: bool,
    pub timed_out: bool,
    pub cancelled: bool,
    pub drained: bool,
    pub elapsed_ms: u128,
}
pub(crate) struct Guard(pub(crate) Child);
impl Guard {
    pub(crate) fn graceful(&mut self) {
        #[cfg(unix)]
        {
            unsafe extern "C" {
                fn kill(pid: i32, signal: i32) -> i32;
            }
            if let Ok(pid) = i32::try_from(self.0.id()) {
                unsafe {
                    kill(-pid, 15);
                }
            }
        }
    }
    pub(crate) fn terminate(&mut self) {
        #[cfg(unix)]
        {
            unsafe extern "C" {
                fn kill(pid: i32, signal: i32) -> i32;
            }
            if let Ok(pid) = i32::try_from(self.0.id()) {
                // Child is launched as its own process group; never target the host group.
                unsafe {
                    kill(-pid, 9);
                }
            }
        }
        let _ = self.0.kill();
    }
}
impl Drop for Guard {
    fn drop(&mut self) {
        self.terminate();
        let _ = self.0.wait();
    }
}
pub(crate) fn run(command: &mut Command, timeout: Duration, limit: usize) -> Result<Output> {
    let started = Instant::now();
    let child = spawn(command)?;
    capture(child, started, timeout, limit, None)
}
pub(crate) fn spawn(command: &mut Command) -> Result<Guard> {
    #[cfg(not(unix))]
    {
        let _ = command;
        Err("bounded job execution currently requires Unix process-group cleanup".into())
    }
    #[cfg(unix)]
    {
        use std::os::unix::process::CommandExt;
        command.process_group(0);
        Ok(Guard(
            command
                .stdin(Stdio::null())
                .stdout(Stdio::piped())
                .stderr(Stdio::piped())
                .spawn()?,
        ))
    }
}
pub(crate) fn capture(
    child: Guard,
    started: Instant,
    timeout: Duration,
    limit: usize,
    cancel: Option<&AtomicBool>,
) -> Result<Output> {
    capture_with(child, started, timeout, limit, cancel, &mut |_, _| Ok(()))
}
pub(crate) fn capture_with(
    mut child: Guard,
    started: Instant,
    timeout: Duration,
    limit: usize,
    cancel: Option<&AtomicBool>,
    on_output: &mut dyn FnMut(usize, &[u8]) -> Result<()>,
) -> Result<Output> {
    let stdout = child.0.stdout.take().ok_or("missing stdout")?;
    let stderr = child.0.stderr.take().ok_or("missing stderr")?;
    let (send, receive) = mpsc::sync_channel(8);
    fn reader(
        mut stream: impl Read + Send + 'static,
        which: usize,
        send: mpsc::SyncSender<(usize, std::io::Result<Vec<u8>>)>,
    ) {
        std::thread::spawn(move || {
            let mut buf = [0u8; 8192];
            loop {
                let result = stream.read(&mut buf).map(|n| buf[..n].to_vec());
                let done = result.as_ref().map_or(true, |b| b.is_empty());
                if send.send((which, result)).is_err() || done {
                    break;
                }
            }
        });
    }
    reader(stdout, 0, send.clone());
    reader(stderr, 1, send);
    let mut buffers = [Vec::new(), Vec::new()];
    let mut closed = 0;
    let mut truncated = false;
    let mut timed_out = false;
    let mut cancelled = false;
    let mut status = None;
    let mut stop_started: Option<Instant> = None;
    let mut forced_at: Option<Instant> = None;
    loop {
        if stop_started.is_none() {
            cancelled = cancel.is_some_and(|c| c.load(Ordering::SeqCst));
            timed_out = !cancelled && started.elapsed() >= timeout;
            if cancelled || timed_out {
                child.graceful();
                stop_started = Some(Instant::now());
            }
        }
        if forced_at.is_none()
            && stop_started.is_some_and(|t| t.elapsed() >= Duration::from_millis(500))
        {
            child.terminate();
            forced_at = Some(Instant::now());
        }
        match receive.recv_timeout(Duration::from_millis(10)) {
            Ok((which, result)) => {
                let bytes = result?;
                if bytes.is_empty() {
                    closed += 1;
                } else {
                    on_output(which, &bytes)?;
                    let remaining = limit.saturating_sub(buffers[which].len());
                    truncated |= bytes.len() > remaining;
                    buffers[which].extend_from_slice(&bytes[..bytes.len().min(remaining)]);
                }
            }
            Err(mpsc::RecvTimeoutError::Timeout) => {}
            Err(mpsc::RecvTimeoutError::Disconnected) => {
                closed = 2;
            }
        }
        if status.is_none() {
            status = child.0.try_wait()?;
        }
        if closed == 2 && status.is_some() {
            break;
        }
        if closed == 2 {
            std::thread::sleep(Duration::from_millis(10));
        }
        // A detached descendant may keep a pipe open despite group cleanup.
        if forced_at.is_some_and(|t| t.elapsed() >= Duration::from_secs(1)) {
            break;
        }
    }
    let status = match status {
        Some(s) => s,
        None => child.0.wait()?,
    };
    let [stdout, stderr] = buffers;
    Ok(Output {
        status,
        stdout,
        stderr,
        truncated,
        timed_out,
        cancelled,
        drained: closed == 2,
        elapsed_ms: started.elapsed().as_millis(),
    })
}
