//! clangd adapter: explicit C++ sources and compile commands in a disposable snapshot.
use crate::{
    Workspace,
    coordinates::{to_byte_offset, to_utf16_position},
    lsp::{Navigation, normalize_locations, scoped_ranges, snapshot_path},
    lsp_transport::Client,
    semantic::{Query, Request},
};
use serde_json::{Value, json};
use std::{
    collections::{BTreeMap, BTreeSet},
    path::Path,
    process::Command,
    time::{Duration, Instant},
};
type Result<T> = std::result::Result<T, Box<dyn std::error::Error>>;
fn uri(path: &Path) -> Result<String> {
    Ok(url::Url::from_file_path(path)
        .map_err(|_| "invalid source URI")?
        .into())
}
pub(crate) fn query(
    workspace: &Workspace,
    request: &Request,
    standard: &str,
    defines: &BTreeMap<String, String>,
) -> Result<Value> {
    if !["c++17", "c++20", "c++23"].contains(&standard)
        || request.files.is_empty()
        || request.files.len() > crate::semantic::MAX_SOURCE_FILES
        || !request.files.contains(&request.path)
    {
        return Err(
            "C++ context requires c++17/20/23, 1..=64 explicit files and a member query path"
                .into(),
        );
    }
    if defines.len() > 32
        || defines.iter().any(|(key, value)| {
            key.is_empty()
                || key.len() > 128
                || !key.bytes().enumerate().all(|(i, b)| {
                    b == b'_' || b.is_ascii_alphabetic() || (i > 0 && b.is_ascii_digit())
                })
                || value.len() > 128
                || !value
                    .bytes()
                    .all(|b| b.is_ascii_alphanumeric() || b == b'_')
        })
    {
        return Err(
            "defines require at most 32 bounded identifier names and ASCII token values".into(),
        );
    }
    if matches!(request.query, Query::Rename) {
        let name = request
            .new_name
            .as_deref()
            .ok_or("rename requires new_name")?;
        if name.is_empty()
            || name.len() > 128
            || !name
                .bytes()
                .enumerate()
                .all(|(i, b)| b == b'_' || b.is_ascii_alphabetic() || (i > 0 && b.is_ascii_digit()))
        {
            return Err("rename requires a bounded ASCII identifier; server acceptance is not compiler validation".into());
        }
    }
    let deadline = Instant::now() + Duration::from_secs(30);
    let temporary = tempfile::tempdir()?;
    let root = temporary.path().canonicalize()?;
    let mut sources = BTreeMap::new();
    let mut hashes = BTreeMap::new();
    let mut names = BTreeMap::new();
    let mut source_bytes = 0;
    for file in &request.files {
        if file.len() > 4096
            || !["cpp", "cc", "cxx", "h", "hpp", "hh"].contains(
                &Path::new(file)
                    .extension()
                    .and_then(|s| s.to_str())
                    .unwrap_or(""),
            )
        {
            return Err("C++ context requires bounded C++ source/header paths".into());
        }
        let (path, bytes) = workspace.read_source_bounded(Path::new(file), 256 * 1024)?;
        if sources.contains_key(&path) {
            return Err("duplicate semantic source".into());
        }
        crate::semantic::account_source(&mut source_bytes, bytes.len())?;
        let source = String::from_utf8(bytes)?;
        let destination = root.join(&path);
        std::fs::create_dir_all(destination.parent().ok_or("missing parent")?)?;
        std::fs::write(destination, &source)?;
        hashes.insert(
            path.clone(),
            blake3::hash(source.as_bytes()).to_hex().to_string(),
        );
        names.insert(file, path.clone());
        sources.insert(path, source);
    }
    // Never execute a compiler from repository metadata. No query-driver or user .clangd configuration.
    let commands: Vec<_> = sources
        .keys()
        .map(|p| {
            let mut args = vec![
                "clang++".to_string(),
                format!("-std={standard}"),
                "-x".into(),
                "c++".into(),
                "-I".into(),
                root.to_string_lossy().into_owned(),
            ];
            args.extend(
                defines
                    .iter()
                    .map(|(key, value)| format!("-D{key}={value}")),
            );
            args.extend(["-c".into(), root.join(p).to_string_lossy().into_owned()]);
            json!({"directory":root,"file":root.join(p),"arguments":args})
        })
        .collect();
    std::fs::write(
        root.join("compile_commands.json"),
        serde_json::to_vec(&commands)?,
    )?;
    let mut command = Command::new("clangd");
    command.current_dir(&root).args([
        "--enable-config=0",
        "--background-index=0",
        "--clang-tidy=0",
        "--header-insertion=never",
        "-j=2",
    ]);
    let mut client = Client::start(&mut command, "clangd", json!({}), deadline)?;
    let initialized=client.request("initialize",json!({"processId":null,"rootUri":uri(&root)?,"capabilities":{"general":{"positionEncodings":["utf-16"]},"offsetEncoding":["utf-16"],"textDocument":{"rename":{"prepareSupport":true},"documentSymbol":{"hierarchicalDocumentSymbolSupport":true},"definition":{"linkSupport":false},"hover":{"contentFormat":["plaintext"]}},"workspace":{"workspaceEdit":{"documentChanges":true}}},"initializationOptions":{"clangdFileStatus":true}}))?;
    if initialized["capabilities"]["positionEncoding"]
        .as_str()
        .unwrap_or("utf-16")
        != "utf-16"
        || initialized["offsetEncoding"].as_str().unwrap_or("utf-16") != "utf-16"
    {
        return Err("clangd did not negotiate UTF-16".into());
    }
    client.send(json!({"method":"initialized","params":{}}))?;
    for (p, s) in &sources {
        client.send(json!({"method":"textDocument/didOpen","params":{"textDocument":{"uri":uri(&root.join(p))?,"languageId":"cpp","version":1,"text":s}}}))?;
    }
    let mut ready = BTreeSet::new();
    let mut diagnostics = BTreeMap::new();
    while ready.len() != sources.len() {
        let message = client.receive()?;
        if message["method"] == "textDocument/clangd.fileStatus"
            && message["params"]["state"] == "idle"
        {
            ready.insert(snapshot_path(
                &root,
                &sources,
                message["params"]["uri"]
                    .as_str()
                    .ok_or("missing status URI")?,
            )?);
        } else if message["method"] == "textDocument/publishDiagnostics" {
            let path = snapshot_path(
                &root,
                &sources,
                message["params"]["uri"]
                    .as_str()
                    .ok_or("missing diagnostic URI")?,
            )?;
            diagnostics.insert(path, message["params"]["diagnostics"].clone());
        }
    }
    let path = &names[&request.path];
    let source = &sources[path];
    let byte = to_byte_offset(source, request.position)?;
    let at = to_utf16_position(source, byte)?;
    let params = json!({"textDocument":{"uri":uri(&root.join(path))?},"position":at});
    let limit = request.limit;
    let locations = |v, kind| -> Result<Value> {
        let v = normalize_locations(v, kind)?;
        let r = scoped_ranges(&root, &sources, v, limit, kind)?;
        Ok(json!({"ranges":r,"matches":r.len()}))
    };
    let mut response = match request.query {
        Query::Definition => locations(
            client.request("textDocument/definition", params)?,
            Navigation::Definition,
        )?,
        Query::References => {
            let mut p = params;
            p["context"] = json!({"includeDeclaration":true});
            locations(
                client.request("textDocument/references", p)?,
                Navigation::References,
            )?
        }
        Query::Hover => crate::lsp_hover::hover(
            path,
            source,
            client.request("textDocument/hover", params)?,
            limit,
        )?,
        Query::Symbols => crate::lsp_symbols::outline(
            path,
            source,
            client.request("textDocument/documentSymbol", params)?,
            limit,
        )?,
        Query::Diagnostics => crate::lsp_diagnostics::diagnostics(
            path,
            source,
            json!({"kind":"full","items":diagnostics.get(path).ok_or("clangd did not publish diagnostics; no clean claim")?}),
            limit,
        )?,
        Query::Inspect => {
            let d = locations(
                client.request("textDocument/definition", params.clone())?,
                Navigation::Definition,
            )?;
            let mut p = params.clone();
            p["context"] = json!({"includeDeclaration":true});
            let r = locations(
                client.request("textDocument/references", p)?,
                Navigation::References,
            )?;
            if d["matches"].as_u64().unwrap_or(0) + r["matches"].as_u64().unwrap_or(0)
                > limit as u64
            {
                return Err("combined location limit exceeded".into());
            }
            let h = crate::lsp_hover::hover(
                path,
                source,
                client.request("textDocument/hover", params)?,
                8192,
            )?;
            json!({"definition":d,"references":r,"hover":h})
        }
        Query::Rename => {
            if initialized["capabilities"]["renameProvider"]["prepareProvider"] != true {
                return Err("server does not advertise prepareRename".into());
            }
            let prepared = crate::lsp_rename::prepare(
                source,
                byte,
                client.request("textDocument/prepareRename", params.clone())?,
            )?;
            let mut p = params;
            p["newName"] = json!(request.new_name);
            let proposal = if prepared.is_null() {
                Value::Null
            } else {
                crate::lsp_rename::proposal(
                    &root,
                    &sources,
                    client.request("textDocument/rename", p)?,
                    limit,
                )?
            };
            json!({"path":path,"prepared":prepared,"new_name":request.new_name,"proposal":proposal})
        }
    };
    for (p, h) in &hashes {
        let (normalized, b) = workspace.read_source_bounded(Path::new(p), 256 * 1024)?;
        if normalized != *p || blake3::hash(&b).to_hex().as_str() != h {
            return Err("source changed during semantic analysis; retry".into());
        }
    }
    client.remaining()?;
    response["file_hashes"] = json!(hashes);
    response["language"] = json!("cpp");
    response["complete"] = json!(false);
    response["compiler_checked"] = json!(false);
    response["semantic_validated"] = json!(false);
    response["analysis_context"] = json!({"language":"cpp","standard":standard,"defines":defines,"files":request.files,"mode":"on_demand_snapshot","all_sources_opened":true});
    response["limitations"] = json!([
        "Only explicit source/header snapshots, the selected standard, explicit token defines and snapshot-root includes are configured. No other project build flags, generated files, dependency discovery or background index. System headers may come from the installed clangd toolchain.",
        "Server proposals and diagnostics are not proof of compilation, complete references or preserved behavior. Review and externally build/test changes."
    ]);
    Ok(response)
}
