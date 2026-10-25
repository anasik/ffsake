//! Bounded JSON-RPC transport shared by language adapters. No language discovery or policy.
use serde_json::{Value, json};
use std::io::{self, BufRead, BufReader, Read, Write};
use std::process::{Child, Command, Stdio};
use std::sync::mpsc::{self, Receiver, SyncSender};
use std::time::{Duration, Instant};
type Result<T> = std::result::Result<T, Box<dyn std::error::Error>>;
type WriteRequest = (Vec<u8>, SyncSender<io::Result<()>>);

fn read_frame(reader: &mut impl BufRead, total: &mut usize) -> io::Result<Value> {
    let invalid = || io::Error::new(io::ErrorKind::InvalidData, "invalid or oversized LSP frame");
    let mut header_bytes = 0;
    let mut length = None;
    loop {
        let mut line = Vec::new();
        let read = Read::by_ref(reader)
            .take((8193 - header_bytes) as u64)
            .read_until(b'\n', &mut line)?;
        header_bytes += read;
        if read == 0 || header_bytes > 8192 || !line.ends_with(b"\r\n") {
            return Err(invalid());
        }
        if line == b"\r\n" {
            break;
        }
        let line = std::str::from_utf8(&line).map_err(|_| invalid())?;
        let (key, value) = line.trim_end().split_once(':').ok_or_else(invalid)?;
        if key.eq_ignore_ascii_case("content-length") {
            if length.is_some() {
                return Err(invalid());
            }
            length = Some(value.trim().parse::<usize>().map_err(|_| invalid())?);
        }
    }
    let length = length
        .filter(|&n| n > 0 && n <= 1024 * 1024)
        .ok_or_else(invalid)?;
    *total += header_bytes + length;
    if *total > 8 * 1024 * 1024 {
        return Err(invalid());
    }
    let mut bytes = vec![0; length];
    reader.read_exact(&mut bytes)?;
    serde_json::from_slice(&bytes).map_err(|_| invalid())
}

fn backend_error(server: &str, method: &str, error: &Value) -> String {
    let code = error["code"]
        .as_i64()
        .and_then(|code| i32::try_from(code).ok());
    match (code, error["message"].as_str()) {
        (Some(code), Some(message)) if message.len() <= 1024 => {
            // Debug quoting escapes control characters; arbitrary error.data is never forwarded.
            format!("{server} rejected {method} ({code}): {message:?}")
        }
        (Some(code), Some(_)) => {
            format!("{server} rejected {method} ({code}): backend message exceeds 1024 bytes")
        }
        _ => format!("{server} rejected {method}: invalid backend error response"),
    }
}

pub(crate) struct Client {
    server: String,
    child: Child,
    outgoing: SyncSender<WriteRequest>,
    incoming: Receiver<std::result::Result<Value, String>>,
    deadline: Instant,
    next: u64,
    options: Value,
}

impl Drop for Client {
    fn drop(&mut self) {
        let _ = self.child.kill();
        let _ = self.child.wait();
    }
}

impl Client {
    pub(crate) fn start(
        command: &mut Command,
        server: &str,
        options: Value,
        deadline: Instant,
    ) -> Result<Self> {
        let mut child = command
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::null())
            .spawn()
            .map_err(|e| format!("cannot start {server}: {e}"))?;
        let mut stdin = child.stdin.take().ok_or("missing LSP stdin")?;
        let stdout = child.stdout.take().ok_or("missing LSP stdout")?;
        let (outgoing, requests) = mpsc::sync_channel::<WriteRequest>(1);
        std::thread::spawn(move || {
            while let Ok((data, reply)) = requests.recv() {
                let result = stdin.write_all(&data).and_then(|()| stdin.flush());
                let failed = result.is_err();
                let _ = reply.send(result);
                if failed {
                    break;
                }
            }
        });
        let (messages, incoming) = mpsc::sync_channel(4);
        std::thread::spawn(move || {
            let mut reader = BufReader::new(stdout);
            let mut total = 0;
            loop {
                let result = read_frame(&mut reader, &mut total).map_err(|e| e.to_string());
                let failed = result.is_err();
                if messages.send(result).is_err() || failed {
                    break;
                }
            }
        });
        Ok(Self {
            server: server.to_owned(),
            child,
            outgoing,
            incoming,
            deadline,
            next: 1,
            options,
        })
    }

    pub(crate) fn remaining(&self) -> Result<Duration> {
        self.deadline
            .checked_duration_since(Instant::now())
            .ok_or_else(|| "LSP request exceeded session deadline".into())
    }

    pub(crate) fn send(&self, mut message: Value) -> Result<()> {
        message["jsonrpc"] = json!("2.0");
        let body = serde_json::to_vec(&message)?;
        let mut data = format!("Content-Length: {}\r\n\r\n", body.len()).into_bytes();
        data.extend(body);
        let (reply, response) = mpsc::sync_channel(1);
        self.outgoing.try_send((data, reply))?;
        response
            .recv_timeout(self.remaining()?)
            .map_err(|_| "LSP write timed out or disconnected")??;
        Ok(())
    }

    pub(crate) fn receive(&self) -> Result<Value> {
        let message = self
            .incoming
            .recv_timeout(self.remaining()?)
            .map_err(|_| "LSP response timed out or disconnected")??;
        if message.get("method").is_some() && message.get("id").is_some() {
            let id = &message["id"];
            match message["method"].as_str() {
                Some("workspace/configuration") => {
                    let items = message["params"]["items"]
                        .as_array()
                        .ok_or("invalid configuration request")?;
                    if items.len() > 32 {
                        return Err("too many configuration items".into());
                    }
                    self.send(json!({"id":id,"result":vec![self.options.clone();items.len()]}))?;
                }
                Some("window/workDoneProgress/create") => {
                    self.send(json!({"id":id,"result":null}))?
                }
                _ => self.send(
                    json!({"id":id,"error":{"code":-32601,"message":"unsupported client request"}}),
                )?,
            }
        }
        Ok(message)
    }

    pub(crate) fn request(&mut self, method: &str, params: Value) -> Result<Value> {
        let id = self.next;
        self.next += 1;
        self.send(json!({"id":id,"method":method,"params":params}))?;
        loop {
            let message = self.receive()?;
            if message.get("method").is_none() && message["id"].as_u64() == Some(id) {
                if message.get("error").is_some() {
                    return Err(backend_error(&self.server, method, &message["error"]).into());
                }
                return message
                    .get("result")
                    .cloned()
                    .ok_or_else(|| "missing LSP result".into());
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn backend_rejections_expose_bounded_reason_without_error_data() {
        let report = backend_error(
            "rust-analyzer",
            "textDocument/prepareRename",
            &json!({
            "code":-32602,"message":"Renaming aliases is currently unsupported",
            "data":{"private":"must not escape"}}),
        );
        assert_eq!(
            report,
            "rust-analyzer rejected textDocument/prepareRename (-32602): \"Renaming aliases is currently unsupported\""
        );
        assert!(!report.contains("must not escape"));
        let controls = backend_error(
            "rust-analyzer",
            "textDocument/hover",
            &json!({"code":-1,"message":"a\nb\u{1b}[31m"}),
        );
        assert!(!controls.contains('\n') && !controls.contains('\u{1b}'));
        assert!(controls.contains("\\n") && controls.contains("\\u{1b}"));
    }

    #[test]
    fn backend_message_budget_counts_utf8_without_truncating() {
        let exact = "é".repeat(512);
        assert!(
            backend_error(
                "rust-analyzer",
                "hover",
                &json!({"code":-1,"message":exact})
            )
            .contains(&exact)
        );
        let oversized = backend_error(
            "rust-analyzer",
            "hover",
            &json!({"code":-1,"message":"é".repeat(513)}),
        );
        assert!(oversized.contains("exceeds 1024 bytes") && !oversized.contains('é'));
    }

    #[test]
    fn malformed_backend_error_never_forwards_payload() {
        for value in [
            Value::Null,
            json!([]),
            json!({"code":"bad","message":"private"}),
            json!({"code":1.5,"message":"private"}),
            json!({"code":i64::MAX,"message":"private"}),
            json!({"code":-1,"message":{"private":"data"}}),
        ] {
            let report = backend_error("rust-analyzer", "hover", &value);
            assert!(report.contains("invalid backend error response"));
            assert!(!report.contains("private"));
        }
    }
    use std::io::Cursor;

    #[test]
    fn bounded_frames_reject_corruption_and_keep_message_boundaries() {
        for bytes in [
            b"Content-Length: 1048577\r\n\r\n".to_vec(),
            b"Content-Length: -1\r\n\r\n".to_vec(),
            b"Content-Length: 2\r\ncontent-length: 2\r\n\r\n{}".to_vec(),
            b"Content-Length: 3\r\n\r\n{}".to_vec(),
            b"Content-Length: 2\r\n\r\nxx".to_vec(),
            vec![b'x'; 8193],
        ] {
            assert!(read_frame(&mut Cursor::new(bytes), &mut 0).is_err());
        }
        let mut reader = Cursor::new(b"content-length: 2\r\n\r\n{}Content-Length: 4\r\n\r\nnull");
        let mut total = 0;
        assert_eq!(read_frame(&mut reader, &mut total).unwrap(), json!({}));
        assert_eq!(read_frame(&mut reader, &mut total).unwrap(), Value::Null);
        assert!(read_frame(&mut reader, &mut total).is_err());
        assert!(
            read_frame(
                &mut Cursor::new(b"Content-Length: 2\r\n\r\n{}"),
                &mut (8 * 1024 * 1024)
            )
            .is_err()
        );
    }
}
