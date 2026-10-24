//! Read-only Rust navigation over an explicit, disposable source snapshot.
use crate::Workspace;
use crate::coordinates::{
    TextPosition, Utf16Position, from_utf16_position, to_byte_offset, to_utf16_position,
};
use crate::search::SearchRange;
use serde_json::{Value, json};
use std::collections::BTreeMap;
use std::io::{self, BufRead, BufReader, Read, Write};
use std::path::Path;
use std::process::{Child, Command, Stdio};
use std::sync::mpsc::{self, Receiver, SyncSender};
use std::time::{Duration, Instant};
use url::Url;

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

struct Client {
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
    fn start(root: &Path, options: Value, deadline: Instant) -> Result<Self> {
        let mut child = Command::new("rust-analyzer")
            .current_dir(root)
            .env("CARGO_NET_OFFLINE", "true")
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::null())
            .spawn()
            .map_err(|e| {
                format!("cannot start rust-analyzer (install its toolchain component): {e}")
            })?;
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
            child,
            outgoing,
            incoming,
            deadline,
            next: 1,
            options,
        })
    }

    fn remaining(&self) -> Result<Duration> {
        self.deadline
            .checked_duration_since(Instant::now())
            .ok_or_else(|| "Rust navigation exceeded 30-second budget".into())
    }

    fn send(&self, mut message: Value) -> Result<()> {
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

    fn receive(&self) -> Result<Value> {
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

    fn request(&mut self, method: &str, params: Value) -> Result<Value> {
        let id = self.next;
        self.next += 1;
        self.send(json!({"id":id,"method":method,"params":params}))?;
        loop {
            let message = self.receive()?;
            if message.get("method").is_none() && message["id"].as_u64() == Some(id) {
                if message.get("error").is_some() {
                    return Err("rust-analyzer rejected the request".into());
                }
                return message
                    .get("result")
                    .cloned()
                    .ok_or_else(|| "missing LSP result".into());
            }
        }
    }
}

fn uri(path: &Path) -> Result<String> {
    Ok(Url::from_file_path(path)
        .map_err(|_| "invalid local file URI")?
        .into())
}

/// No mutation capability is returned. Results describe only this explicit
/// dependency-free snapshot, not a complete Cargo build configuration.
pub fn rust_references(
    workspace: &Workspace,
    files: &[String],
    crate_root: &str,
    path: &str,
    position: TextPosition,
    edition: &str,
    limit: usize,
) -> Result<Value> {
    navigate(
        workspace,
        Query {
            files,
            crate_root,
            path,
            position,
            edition,
            limit,
        },
        Navigation::References,
    )
}

/// Find definition locations under the same explicit snapshot contract.
pub fn rust_definition(
    workspace: &Workspace,
    files: &[String],
    crate_root: &str,
    path: &str,
    position: TextPosition,
    edition: &str,
    limit: usize,
) -> Result<Value> {
    navigate(
        workspace,
        Query {
            files,
            crate_root,
            path,
            position,
            edition,
            limit,
        },
        Navigation::Definition,
    )
}

/// Get bounded hover/type documentation under the same explicit snapshot contract.
pub fn rust_hover(
    workspace: &Workspace,
    files: &[String],
    crate_root: &str,
    path: &str,
    position: TextPosition,
    edition: &str,
    max_bytes: usize,
) -> Result<Value> {
    navigate(
        workspace,
        Query {
            files,
            crate_root,
            path,
            position,
            edition,
            limit: max_bytes,
        },
        Navigation::Hover,
    )
}

/// Find implementation locations under the same explicit snapshot contract.
pub fn rust_implementation(
    workspace: &Workspace,
    files: &[String],
    crate_root: &str,
    path: &str,
    position: TextPosition,
    edition: &str,
    limit: usize,
) -> Result<Value> {
    navigate(
        workspace,
        Query {
            files,
            crate_root,
            path,
            position,
            edition,
            limit,
        },
        Navigation::Implementation,
    )
}

/// Return native primary diagnostics, not a compiler/build result.
pub fn rust_diagnostics(
    workspace: &Workspace,
    files: &[String],
    crate_root: &str,
    path: &str,
    edition: &str,
    limit: usize,
) -> Result<Value> {
    navigate(
        workspace,
        Query {
            files,
            crate_root,
            path,
            position: TextPosition { line: 1, column: 0 },
            edition,
            limit,
        },
        Navigation::Diagnostics,
    )
}

/// Return a bounded hierarchical outline for one file in the explicit snapshot.
pub fn rust_symbols(
    workspace: &Workspace,
    files: &[String],
    crate_root: &str,
    path: &str,
    edition: &str,
    limit: usize,
) -> Result<Value> {
    navigate(
        workspace,
        Query {
            files,
            crate_root,
            path,
            position: TextPosition { line: 1, column: 0 },
            edition,
            limit,
        },
        Navigation::Symbols,
    )
}

#[derive(Clone, Copy)]
enum Navigation {
    References,
    Definition,
    Implementation,
    Symbols,
    Hover,
    Diagnostics,
}

struct Query<'a> {
    files: &'a [String],
    crate_root: &'a str,
    path: &'a str,
    position: TextPosition,
    edition: &'a str,
    limit: usize,
}

fn navigate(workspace: &Workspace, query: Query<'_>, kind: Navigation) -> Result<Value> {
    let Query {
        files,
        crate_root,
        path,
        position,
        edition,
        limit,
    } = query;
    let maximum = if matches!(kind, Navigation::Hover) {
        65_536
    } else {
        1000
    };
    if files.is_empty()
        || files.len() > 32
        || !(1..=maximum).contains(&limit)
        || !matches!(edition, "2015" | "2018" | "2021" | "2024")
    {
        return Err(
            format!("Rust navigation requires 1..=32 files, a result bound in 1..={maximum} and a supported edition").into(),
        );
    }
    if !files.iter().any(|p| p == crate_root) || !files.iter().any(|p| p == path) {
        return Err("crate_root and query path must be explicit members of files".into());
    }
    let deadline = Instant::now() + Duration::from_secs(30);
    let snapshot = tempfile::tempdir()?;
    let root = snapshot.path().canonicalize()?;
    let mut sources = BTreeMap::new();
    let mut hashes = BTreeMap::new();
    let mut names = BTreeMap::new();
    for file in files {
        if file.len() > 4096 || Path::new(file).extension().is_none_or(|ext| ext != "rs") {
            return Err("Rust navigation requires .rs paths of at most 4096 bytes".into());
        }
        let (normalized, bytes) = workspace.read_source_bounded(Path::new(file), 256 * 1024)?;
        let source = String::from_utf8(bytes)?;
        if sources.contains_key(&normalized) {
            return Err("duplicate Rust navigation file".into());
        }
        let destination = root.join(&normalized);
        std::fs::create_dir_all(destination.parent().ok_or("invalid source parent")?)?;
        std::fs::write(destination, &source)?;
        hashes.insert(
            normalized.clone(),
            blake3::hash(source.as_bytes()).to_hex().to_string(),
        );
        sources.insert(normalized.clone(), source);
        names.insert(file.as_str(), normalized);
    }
    let query = &names[path];
    let query_source = &sources[query];
    let byte = to_byte_offset(query_source, position)?;
    let position = to_utf16_position(query_source, byte)?;
    let options = json!({
        "linkedProjects":[{"crates":[{"root_module":root.join(&names[crate_root]),"edition":edition,
            "deps":[],"cfg":[],"env":{},"is_workspace_member":true,
            "source":{"include_dirs":[root],"exclude_dirs":[]}}]}],
        "cargo":{"buildScripts":{"enable":false},"noDeps":true,"sysroot":null},
        "procMacro":{"enable":false},"checkOnSave":false,
        "diagnostics":{"enable":true,"experimental":{"enable":true}},"cachePriming":{"enable":false},"numThreads":2
    });
    let mut client = Client::start(&root, options.clone(), deadline)?;
    let initialized = client.request("initialize", json!({"processId":null,"rootUri":uri(&root)?,
        "capabilities":{"general":{"positionEncodings":["utf-16"]},"textDocument":{"diagnostic":{"relatedDocumentSupport":false},"hover":{"contentFormat":["markdown","plaintext"]},"definition":{"linkSupport":false},"implementation":{"linkSupport":false},"documentSymbol":{"hierarchicalDocumentSymbolSupport":true,"symbolKind":{"valueSet":(1..=26).collect::<Vec<_>>()}}},"experimental":{"serverStatusNotification":true}},
        "initializationOptions":options}))?;
    if initialized["capabilities"]["positionEncoding"]
        .as_str()
        .unwrap_or("utf-16")
        != "utf-16"
    {
        return Err("rust-analyzer did not negotiate UTF-16".into());
    }
    client.send(json!({"method":"initialized","params":{}}))?;
    loop {
        let message = client.receive()?;
        if message["method"] == "experimental/serverStatus"
            && message["params"]["quiescent"] == true
        {
            if message["params"]["health"] != "ok" {
                return Err("rust-analyzer snapshot is not healthy".into());
            }
            break;
        }
    }
    // Opening files before project loading finishes can leave their source roots
    // classified as external and silently suppress native diagnostics.
    for (path, source) in &sources {
        client.send(
            json!({"method":"textDocument/didOpen","params":{"textDocument":{
            "uri":uri(&root.join(path))?,"languageId":"rust","version":1,"text":source}}}),
        )?;
    }
    let mut parameters =
        json!({"textDocument":{"uri":uri(&root.join(query))?},"position":position});
    let (method, query_kind) = match kind {
        Navigation::References => {
            parameters["context"] = json!({"includeDeclaration":true});
            ("textDocument/references", "references")
        }
        Navigation::Definition => ("textDocument/definition", "definition"),
        Navigation::Implementation => ("textDocument/implementation", "implementation"),
        Navigation::Hover => ("textDocument/hover", "hover"),
        Navigation::Symbols | Navigation::Diagnostics => {
            parameters
                .as_object_mut()
                .ok_or("invalid symbol request")?
                .remove("position");
            if matches!(kind, Navigation::Diagnostics) {
                ("textDocument/diagnostic", "diagnostics")
            } else {
                ("textDocument/documentSymbol", "symbols")
            }
        }
    };
    if matches!(kind, Navigation::Diagnostics)
        && !initialized["capabilities"]["diagnosticProvider"].is_object()
    {
        return Err("rust-analyzer does not advertise pull diagnostics".into());
    }
    let result = client.request(method, parameters)?;
    let mut response = if matches!(kind, Navigation::Diagnostics) {
        crate::lsp_diagnostics::diagnostics(query, query_source, result, limit)?
    } else if matches!(kind, Navigation::Symbols) {
        crate::lsp_symbols::outline(query, query_source, result, limit)?
    } else if matches!(kind, Navigation::Hover) {
        crate::lsp_hover::hover(query, query_source, result, limit)?
    } else {
        let locations = normalize_locations(result, kind)?;
        let ranges = scoped_ranges(&root, &sources, locations, limit, kind)?;
        json!({"ranges":ranges,"matches":ranges.len()})
    };
    for (path, expected) in &hashes {
        let (normalized, bytes) = workspace.read_source_bounded(Path::new(path), 256 * 1024)?;
        if &normalized != path || blake3::hash(&bytes).to_hex().as_str() != expected {
            return Err("source changed during Rust navigation analysis; retry".into());
        }
    }
    client.remaining()?;
    response["file_hashes"] = json!(hashes);
    response["analysis_mode"] = json!("explicit_crate_snapshot");
    response["query"] = json!(query_kind);
    response["complete"] = json!(false);
    response["limitations"] = json!([
        "No Cargo dependencies, sysroot, build scripts, procedural macros or configured features are loaded.",
        "Results are read-only navigation data, not a semantic rename capability."
    ]);
    if matches!(kind, Navigation::Diagnostics) {
        response["compiler_checked"] = json!(false);
        response["experimental_diagnostics"] = json!(true);
        response["limitations"].as_array_mut().ok_or("invalid limitations")?.push(json!(
            "Native primary diagnostics only. Missing dependencies or configuration can cause semantic errors; an empty result does not prove the project compiles."
        ));
    }
    Ok(response)
}

fn normalize_locations(value: Value, kind: Navigation) -> Result<Value> {
    match value {
        Value::Null | Value::Array(_) => Ok(value),
        Value::Object(_)
            if matches!(kind, Navigation::Definition | Navigation::Implementation)
                && value.get("uri").is_some()
                && value.get("range").is_some() =>
        {
            Ok(json!([value]))
        }
        _ => Err("invalid LSP navigation locations (LocationLink was not negotiated)".into()),
    }
}

fn scoped_ranges(
    root: &Path,
    sources: &BTreeMap<String, String>,
    locations: Value,
    limit: usize,
    kind: Navigation,
) -> Result<Vec<SearchRange>> {
    let locations = if locations.is_null() {
        vec![]
    } else {
        locations.as_array().ok_or("invalid LSP locations")?.clone()
    };
    if locations.len() > limit {
        return Err("Rust navigation limit exceeded; no partial result returned".into());
    }
    let mut ranges = Vec::new();
    for location in locations {
        let url = Url::parse(location["uri"].as_str().ok_or("missing LSP URI")?)?;
        if url.scheme() != "file"
            || url.host_str().is_some()
            || url.query().is_some()
            || url.fragment().is_some()
        {
            return Err("non-local LSP result".into());
        }
        let absolute = url.to_file_path().map_err(|_| "invalid LSP file URI")?;
        let path = absolute
            .strip_prefix(root)
            .map_err(|_| "LSP result outside snapshot")?
            .to_str()
            .ok_or("non-UTF-8 LSP result path")?
            .to_string();
        let source = sources
            .get(&path)
            .ok_or("LSP result outside explicit source files")?;
        let start = from_utf16_position(
            source,
            serde_json::from_value::<Utf16Position>(location["range"]["start"].clone())?,
        )?;
        let end = from_utf16_position(
            source,
            serde_json::from_value::<Utf16Position>(location["range"]["end"].clone())?,
        )?;
        if start > end || (start == end && matches!(kind, Navigation::References)) {
            return Err("invalid LSP navigation range".into());
        }
        ranges.push(SearchRange {
            path,
            start_byte: start,
            end_byte: end,
            line: location["range"]["start"]["line"]
                .as_u64()
                .ok_or("invalid LSP line")?
                + 1,
        });
    }
    ranges.sort();
    ranges.dedup();
    Ok(ranges)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Cursor;

    #[test]
    fn navigation_responses_accept_locations_but_not_unnegotiated_links() {
        let location = json!({"uri":"file:///snapshot/main.rs","range":{
            "start":{"line":0,"character":3},"end":{"line":0,"character":7}}});
        assert_eq!(
            normalize_locations(location.clone(), Navigation::Definition).unwrap(),
            json!([location.clone()])
        );
        assert_eq!(
            normalize_locations(location.clone(), Navigation::Implementation).unwrap(),
            json!([location.clone()])
        );
        assert!(normalize_locations(location.clone(), Navigation::References).is_err());
        assert_eq!(
            normalize_locations(json!([location.clone()]), Navigation::Definition).unwrap(),
            json!([location])
        );
        assert!(
            normalize_locations(Value::Null, Navigation::Definition)
                .unwrap()
                .is_null()
        );
        for invalid in [
            json!(42),
            json!({}),
            json!({"targetUri":"file:///snapshot/main.rs","targetSelectionRange":{}}),
        ] {
            assert!(normalize_locations(invalid, Navigation::Definition).is_err());
        }
    }

    #[test]
    fn empty_module_definition_is_a_valid_anchor_not_a_reference() {
        let temp = tempfile::tempdir().unwrap();
        let root = temp.path().canonicalize().unwrap();
        let sources = BTreeMap::from([("empty.rs".to_string(), String::new())]);
        let locations = json!([{"uri":uri(&root.join("empty.rs")).unwrap(),"range":{
            "start":{"line":0,"character":0},"end":{"line":0,"character":0}}}]);
        assert!(
            scoped_ranges(
                &root,
                &sources,
                locations.clone(),
                10,
                Navigation::References
            )
            .is_err()
        );
        let result = scoped_ranges(&root, &sources, locations, 10, Navigation::Definition).unwrap();
        assert_eq!(result[0].start_byte, 0);
        assert_eq!(result[0].end_byte, 0);
    }

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

    #[test]
    fn results_are_confined_to_snapshot_sources_and_exact_utf16_ranges() {
        let temp = tempfile::tempdir().unwrap();
        let root = temp.path().canonicalize().unwrap();
        let source = "// 🚀\r\nlet account = 1;";
        let sources = BTreeMap::from([("space é.rs".to_string(), source.to_string())]);
        let valid = json!({"uri":uri(&root.join("space é.rs")).unwrap(),"range":{
            "start":{"line":1,"character":4},"end":{"line":1,"character":11}}});
        let ranges = scoped_ranges(
            &root,
            &sources,
            json!([valid.clone(), valid.clone()]),
            2,
            Navigation::References,
        )
        .unwrap();
        assert_eq!(ranges.len(), 1);
        assert_eq!(&source[ranges[0].start_byte..ranges[0].end_byte], "account");
        assert_eq!(ranges[0].line, 2);
        assert!(
            scoped_ranges(
                &root,
                &sources,
                json!([valid.clone(), valid.clone()]),
                1,
                Navigation::References
            )
            .is_err()
        );
        for path in [
            root.join("missing.rs"),
            root.join(".ffsake/private.rs"),
            root.parent().unwrap().join("outside.rs"),
        ] {
            let mut bad = valid.clone();
            bad["uri"] = json!(uri(&path).unwrap());
            assert!(
                scoped_ranges(&root, &sources, json!([bad]), 10, Navigation::References).is_err()
            );
        }
        for url in ["https://example.org/source.rs", "file://remote/source.rs"] {
            let mut bad = valid.clone();
            bad["uri"] = json!(url);
            assert!(
                scoped_ranges(&root, &sources, json!([bad]), 10, Navigation::References).is_err()
            );
        }
        for (start, end) in [
            (
                json!({"line":0,"character":4}),
                json!({"line":0,"character":5}),
            ),
            (
                json!({"line":1,"character":11}),
                json!({"line":1,"character":4}),
            ),
            (
                json!({"line":9,"character":0}),
                json!({"line":9,"character":1}),
            ),
        ] {
            let mut bad = valid.clone();
            bad["range"] = json!({"start":start,"end":end});
            assert!(
                scoped_ranges(&root, &sources, json!([bad]), 10, Navigation::References).is_err()
            );
        }
        assert!(
            scoped_ranges(&root, &sources, Value::Null, 10, Navigation::References)
                .unwrap()
                .is_empty()
        );
    }

    #[test]
    fn invalid_snapshot_inputs_reject_before_launching_analyzer() {
        let temp = tempfile::tempdir().unwrap();
        let workspace = Workspace::new(temp.path()).unwrap();
        let source = "fn main() {}";
        std::fs::write(temp.path().join("main.rs"), source).unwrap();
        for files in [
            vec![],
            vec!["main.rs".into(); 33],
            vec!["missing.rs".into()],
            vec!["../main.rs".into()],
            vec![".ffsake/private.rs".into()],
            vec!["main.rs".into(), "./main.rs".into()],
        ] {
            let main = files.first().cloned().unwrap_or_default();
            assert!(
                rust_references(
                    &workspace,
                    &files,
                    &main,
                    &main,
                    TextPosition { line: 1, column: 0 },
                    "2024",
                    10
                )
                .is_err()
            );
        }
        assert!(
            rust_references(
                &workspace,
                &["main.rs".into()],
                "main.rs",
                "main.rs",
                TextPosition { line: 0, column: 0 },
                "2024",
                10
            )
            .is_err()
        );
        assert!(
            rust_references(
                &workspace,
                &["main.rs".into()],
                "main.rs",
                "main.rs",
                TextPosition { line: 1, column: 0 },
                "2030",
                10
            )
            .is_err()
        );
        assert_eq!(
            std::fs::read_to_string(temp.path().join("main.rs")).unwrap(),
            source
        );
    }
}
