//! Host-visible one-shot CLI for the guarded workspace operations.
//!
//! The CLI reuses the resident request surface and access policy, so
//! authorization, stale-state checks and journal history are identical to the
//! MCP server. Preview/apply stays two commands: `--request` with a preview op
//! prints the diff and stores a private token; `--apply TOKEN` replays the same
//! request against current files, applies the resulting handle and deletes the
//! token. A changed file therefore rejects instead of applying stale content.
use fff_workspace::{
    Workspace,
    resident::{Request, Resident},
    semantic::{Query as SemanticQuery, Request as SemanticRequest},
};
use serde_json::{Value, json};
use std::{
    error::Error,
    fs::{self, OpenOptions},
    io::Write,
    path::{Path, PathBuf},
    time::{SystemTime, UNIX_EPOCH},
};

const TOKEN_VERSION: u64 = 1;
const TOKEN_MAX_AGE_SECONDS: u64 = 3600;
const TOKEN_MAX_BYTES: usize = 16 * 1024 * 1024;

type Result<T> = std::result::Result<T, Box<dyn Error>>;

fn usage() -> ! {
    eprintln!(
        "usage: ffsake WORKSPACE [--access FILE] [--debug FILE] \
         (--request JSON | --apply TOKEN)\n\
         \n\
         --request executes one guarded operation and prints JSON. Preview requests\n\
         (preview_edit, preview_files, preview_create, preview_copy, preview_move,\n\
         preview_delete, preview_rename, preview_semantic_rename) also print a token\n\
         for --apply. preview_semantic_rename runs the installed language server,\n\
         previews the exact rename proposal and stores the discovery request with\n\
         its source hashes; --apply repeats discovery and rejects changed sources.\n\
         Examples:\n\
         ffsake . --request '{{\"op\":\"read_files\",\"files\":[{{\"path\":\"src/lib.rs\"}}]}}'\n\
         ffsake . --request '{{\"op\":\"preview_files\",\"operations\":[{{\"op\":\"edit\",\"path\":\"src/lib.rs\",\"old_text\":\"a\",\"new_text\":\"b\",\"expected_matches\":1}}]}}'\n\
         ffsake . --apply <token>\n\
         ffsake . --request '{{\"op\":\"undo\",\"transaction\":\"<id>\"}}'\n\
         ffsake . --debug .ffsake/debug.json --request '{{\"op\":\"debug_probe\",\"configuration\":\"app\",\"breakpoints\":[{{\"path\":\"main.py\",\"lines\":1}}],\"stops\":2}}'\n\
         \n\
         Persistent debug sessions, task jobs and Git review handles stay on the MCP server."
    );
    std::process::exit(2)
}

fn main() {
    let result = run();
    match result {
        Ok(value) => println!("{}", serde_json::to_string(&value).unwrap_or_default()),
        Err(error) => {
            println!("{}", json!({ "ok": false, "error": error.to_string() }));
            std::process::exit(1);
        }
    }
}

fn run() -> Result<Value> {
    let args = std::env::args().skip(1).collect::<Vec<_>>();
    if args.first().is_some_and(|arg| arg == "serve") {
        return serve(&args);
    }
    if args
        .first()
        .is_some_and(|arg| matches!(arg.as_str(), "read" | "edit" | "apply" | "probe"))
    {
        return subcommand(&args);
    }
    let root = args.first().cloned().unwrap_or_else(|| usage());
    let mut access_policy: Option<PathBuf> = None;
    let mut debug_policy: Option<PathBuf> = None;
    let mut request_json: Option<String> = None;
    let mut apply_token: Option<String> = None;
    let mut index = 1;
    while index < args.len() {
        let flag = &args[index];
        let value = args.get(index + 1).cloned().unwrap_or_else(|| usage());
        match flag.as_str() {
            "--access" if access_policy.is_none() => access_policy = Some(value.into()),
            "--debug" if debug_policy.is_none() => debug_policy = Some(value.into()),
            "--request" if request_json.is_none() => request_json = Some(value),
            "--apply" if apply_token.is_none() => apply_token = Some(value),
            "--help" | "-h" => usage(),
            _ => usage(),
        }
        index += 2;
    }
    if request_json.is_some() == apply_token.is_some() {
        usage();
    }

    let mut workspace = Workspace::new(&root)?;
    if let Some(path) = access_policy {
        workspace.configure_access(&path)?;
    } else {
        workspace.configure_persistent_access()?;
    }
    let mut runtime = Resident::open(workspace)?;
    if let Some(path) = debug_policy {
        runtime.configure_debug(fff_workspace::debug::Configurations::load(&path)?);
    }
    let canonical_root = fs::canonicalize(&root)?;

    match (request_json, apply_token) {
        (Some(text), None) => {
            let mut value: Value = serde_json::from_str(&text)?;
            if value.get("op").and_then(Value::as_str) == Some(SEMANTIC_RENAME_OP) {
                let result = semantic_rename_preview(&mut runtime, &mut value)?;
                let token = store_token(&canonical_root, &serde_json::to_string(&value)?)?;
                return Ok(json!({ "ok": true, "result": result, "token": token }));
            }
            if value.get("op").and_then(Value::as_str) == Some("preview_rust_rename") {
                return Err("preview_rust_rename needs a resident rename_set; use \
                            preview_semantic_rename for one-shot semantic rename or \
                            run 'ffsake serve'"
                    .into());
            }
            let preview = is_preview(&value);
            let request: Request = serde_json::from_value(value)?;
            if preview {
                let result = runtime.execute(request)?;
                let token = store_token(&canonical_root, &text)?;
                Ok(json!({ "ok": true, "result": result, "token": token }))
            } else {
                Ok(json!({ "ok": true, "result": runtime.execute(request)? }))
            }
        }
        (None, Some(token)) => {
            let text = load_token(&canonical_root, &token)?;
            let mut value: Value = serde_json::from_str(&text)?;
            if !is_preview(&value) {
                return Err("token does not contain a preview request".into());
            }
            let preview = if value.get("op").and_then(Value::as_str) == Some(SEMANTIC_RENAME_OP) {
                semantic_rename_preview(&mut runtime, &mut value)?
            } else {
                let request: Request = serde_json::from_value(value)?;
                runtime.execute(request)?
            };
            let handle = preview
                .get("handle")
                .and_then(Value::as_u64)
                .ok_or("preview did not return an apply handle")?;
            let result = runtime.execute(Request::Apply { handle })?;
            let _ = remove_token(&canonical_root, &token);
            Ok(json!({
                "ok": true,
                "transaction": result["transaction"],
                "changed_files": result["changed_files"],
            }))
        }
        _ => usage(),
    }
}

fn runtime_for(root: &str, access: Option<&Path>, debug: Option<&Path>) -> Result<Resident> {
    let mut workspace = Workspace::new(root)?;
    if let Some(path) = access {
        workspace.configure_access(path)?;
    } else {
        workspace.configure_persistent_access()?;
    }
    let mut runtime = Resident::open(workspace)?;
    if let Some(path) = debug {
        runtime.configure_debug(fff_workspace::debug::Configurations::load(path)?);
    }
    Ok(runtime)
}

fn read_spec(spec: &str) -> (String, Option<usize>, Option<usize>) {
    if let Some((path, range)) = spec.rsplit_once(':')
        && let Some((start, end)) = range.split_once('-')
        && let (Ok(start), Ok(end)) = (start.parse::<usize>(), end.parse::<usize>())
    {
        return (path.to_string(), Some(start), Some(end));
    }
    (spec.to_string(), None, None)
}

fn line_range(args: &[String]) -> Option<(usize, usize)> {
    let mut index = 0;
    while index + 1 < args.len() {
        match args[index].as_str() {
            "--line" => {
                if let Ok(line) = args[index + 1].parse::<usize>() {
                    return Some((line, line));
                }
            }
            "--lines" => {
                if let Some((start, end)) = args[index + 1].split_once('-')
                    && let (Ok(start), Ok(end)) = (start.parse::<usize>(), end.parse::<usize>())
                {
                    return Some((start, end));
                }
            }
            _ => {}
        }
        index += 1;
    }
    None
}

fn edit_operations(args: &[String]) -> Result<Vec<Value>> {
    let mut operations = Vec::new();
    if args.first().map(String::as_str) == Some("--edit") {
        let mut index = 0;
        while index < args.len() {
            if args[index] != "--edit" {
                return Err("batch edit requires --edit PATH --old TEXT --new TEXT".into());
            }
            let path = args
                .get(index + 1)
                .cloned()
                .ok_or("--edit requires a path")?;
            index += 2;
            let mut old_text = None;
            let mut new_text = None;
            let mut count = 1usize;
            while index < args.len() && args[index] != "--edit" {
                match args[index].as_str() {
                    "--old" if index + 1 < args.len() => {
                        old_text = Some(args[index + 1].clone());
                        index += 2;
                    }
                    "--new" if index + 1 < args.len() => {
                        new_text = Some(args[index + 1].clone());
                        index += 2;
                    }
                    "--count" if index + 1 < args.len() => {
                        count = args[index + 1].parse()?;
                        index += 2;
                    }
                    "--line" | "--lines" if index + 1 < args.len() => {
                        index += 2;
                    }
                    _ => return Err("batch edit accepts --old, --new and --count".into()),
                }
            }
            operations.push(json!({
                "op":"edit","path":path,
                "old_text":old_text.ok_or("missing --old")?,
                "new_text":new_text.ok_or("missing --new")?,
                "expected_matches":count
            }));
        }
        return Ok(operations);
    }
    let path = args
        .first()
        .cloned()
        .ok_or("edit requires PATH --old TEXT --new TEXT")?;
    let mut old_text = None;
    let mut new_text = None;
    let mut count = 1usize;
    let mut index = 1;
    while index + 1 < args.len() {
        match args[index].as_str() {
            "--old" => old_text = Some(args[index + 1].clone()),
            "--new" => new_text = Some(args[index + 1].clone()),
            "--count" => count = args[index + 1].parse()?,
            "--line" | "--lines" => {}
            _ => return Err("edit accepts --old, --new and --count".into()),
        }
        index += 2;
    }
    operations.push(json!({
        "op":"edit","path":path,
        "old_text":old_text.ok_or("edit requires --old")?,
        "new_text":new_text.ok_or("edit requires --new")?,
        "expected_matches":count
    }));
    Ok(operations)
}

fn subcommand(args: &[String]) -> Result<Value> {
    let command = args.first().map(String::as_str).unwrap_or_else(|| usage());
    let root = args.get(1).cloned().unwrap_or_else(|| usage());
    let mut rest = args[2..].to_vec();
    let mut access: Option<PathBuf> = None;
    let mut debug: Option<PathBuf> = None;
    let mut filtered = Vec::new();
    let mut index = 0;
    while index < rest.len() {
        match rest[index].as_str() {
            "--access" if index + 1 < rest.len() => {
                access = Some(rest[index + 1].clone().into());
                index += 2;
            }
            "--debug" if index + 1 < rest.len() => {
                debug = Some(rest[index + 1].clone().into());
                index += 2;
            }
            _ => {
                filtered.push(rest[index].clone());
                index += 1;
            }
        }
    }
    rest = filtered;
    let mut runtime = runtime_for(&root, access.as_deref(), debug.as_deref())?;
    match command {
        "read" => {
            let raw = rest.iter().any(|arg| arg == "--raw");
            rest.retain(|arg| arg != "--raw");
            if rest.is_empty() {
                return Err("read requires at least one path".into());
            }
            let files = rest
                .iter()
                .map(|spec| {
                    let (path, start, end) = read_spec(spec);
                    match (start, end) {
                        (Some(start), Some(end)) => {
                            json!({"path": path, "start": start, "end": end})
                        }
                        _ => json!({"path": path}),
                    }
                })
                .collect::<Vec<_>>();
            let result = runtime.execute(Request::ReadFiles {
                files: serde_json::from_value(Value::Array(files))?,
            })?;
            let mut text = String::new();
            let mut raw_text = String::new();
            for item in result["files"].as_array().cloned().unwrap_or_default() {
                let path = item["path"].as_str().unwrap_or("");
                if item["status"].as_str() != Some("ok") {
                    text.push_str(&format!("{path}: missing\n"));
                    continue;
                }
                let start = item["read"]["start_line"].as_u64().unwrap_or(1) as usize;
                raw_text.push_str(item["read"]["text"].as_str().unwrap_or(""));
                raw_text.push('\n');
                text.push_str(&format!("{path}:\n"));
                for (offset, line) in item["read"]["text"]
                    .as_str()
                    .unwrap_or("")
                    .lines()
                    .enumerate()
                {
                    text.push_str(&format!("{}: {line}\n", start + offset));
                }
            }
            if raw {
                Ok(json!({"ok": true, "text": raw_text}))
            } else {
                Ok(json!({"ok": true, "text": text}))
            }
        }
        "edit" => {
            let range = line_range(&rest);
            let operations = edit_operations(&rest)?;
            if let Some((start, end)) = range {
                if operations.len() != 1 {
                    return Err("--line/--lines requires a single-file edit".into());
                }
                let path = operations[0]["path"].as_str().unwrap_or("").to_string();
                let old_text = operations[0]["old_text"].as_str().unwrap_or("").to_string();
                let scoped = runtime.execute(Request::Read {
                    path: path.clone(),
                    start: Some(start),
                    end: Some(end),
                })?;
                let whole = runtime.execute(Request::Read {
                    path,
                    start: None,
                    end: None,
                })?;
                let count_in = |text: &str| text.matches(&old_text).count();
                if count_in(scoped["text"].as_str().unwrap_or("")) != 1
                    || count_in(whole["text"].as_str().unwrap_or("")) != 1
                {
                    return Err(format!(
                        "line-addressed edit requires exactly one old_text match in lines {start}-{end} and in the file"
                    ).into());
                }
            }
            let request = json!({"op":"preview_files","operations":operations});
            let preview = runtime.execute(serde_json::from_value(request.clone())?)?;
            let token = store_token(&fs::canonicalize(&root)?, &serde_json::to_string(&request)?)?;
            let diff = preview["preview"]["files"]
                .as_array()
                .map(|files| {
                    files
                        .iter()
                        .filter_map(|file| file["unified_diff"].as_str())
                        .collect::<Vec<_>>()
                        .join("\n")
                })
                .unwrap_or_default();
            Ok(json!({"ok": true, "diff": diff, "token": token}))
        }
        "apply" => {
            let Some(token) = rest.first().cloned() else {
                return Err("apply requires TOKEN".into());
            };
            let text = load_token(&fs::canonicalize(&root)?, &token)?;
            let request: Request = serde_json::from_str(&text)?;
            let preview = runtime.execute(request)?;
            let handle = preview
                .get("handle")
                .and_then(Value::as_u64)
                .ok_or("preview did not return an apply handle")?;
            let result = runtime.execute(Request::Apply { handle })?;
            let _ = remove_token(&fs::canonicalize(&root)?, &token);
            Ok(json!({
                "ok": true,
                "transaction": result["transaction"],
                "changed_files": result["changed_files"],
            }))
        }
        "probe" => {
            let mut configuration = None;
            let mut breakpoints = Vec::new();
            let mut stops = 1usize;
            let mut index = 0;
            while index + 1 < rest.len() {
                match rest[index].as_str() {
                    "--config" => configuration = Some(rest[index + 1].clone()),
                    "--breakpoint" => {
                        let spec = &rest[index + 1];
                        let (path, line) = spec
                            .rsplit_once(':')
                            .ok_or("breakpoint must be PATH:LINE")?;
                        breakpoints.push(json!({"path": path, "lines": line.parse::<u32>()?}));
                    }
                    "--stops" => stops = rest[index + 1].parse()?,
                    _ => return Err("probe accepts --config, --breakpoint and --stops".into()),
                }
                index += 2;
            }
            let configuration = configuration.ok_or("probe requires --config")?;
            if breakpoints.is_empty() {
                return Err("probe requires at least one --breakpoint".into());
            }
            let request = json!({"op":"debug_probe","configuration":configuration,
                "breakpoints":breakpoints,"stops":stops});
            let result = runtime.execute(serde_json::from_value(request)?)?;
            let mut inspections = Vec::new();
            if let Some(inspection) = result.pointer("/result/result/inspection") {
                inspections.push(inspection.clone());
            } else if let Some(inspection) = result.pointer("/result/inspection") {
                inspections.push(inspection.clone());
            }
            for key in [
                "next_inspection",
                "next2_inspection",
                "stop_inspection_2",
                "stop_inspection_3",
                "stop_inspection_4",
            ] {
                if result.get(key).is_some() {
                    inspections.push(result[key].clone());
                }
            }
            let mut stops = Vec::new();
            for inspection in inspections {
                let mut values = serde_json::Map::new();
                for scope in inspection["scopes"].as_array().cloned().unwrap_or_default() {
                    for variable in scope["variables"].as_array().cloned().unwrap_or_default() {
                        let name = variable["name"].as_str().unwrap_or("");
                        let value = variable["value"].as_str().unwrap_or("");
                        if !name.is_empty() && !value.is_empty() {
                            values.insert(name.to_string(), Value::String(value.to_string()));
                        }
                    }
                }
                stops.push(json!({
                    "reason": inspection["stop"]["reason"],
                    "line": inspection["frames"][0]["line"],
                    "values": values,
                }));
            }
            Ok(json!({"ok": true, "cleaned": result.get("cleaned"), "stops": stops}))
        }
        _ => usage(),
    }
}

fn serve(args: &[String]) -> Result<Value> {
    let root = args.get(1).cloned().unwrap_or_else(|| usage());
    let mut access_policy: Option<PathBuf> = None;
    let mut debug_policy: Option<PathBuf> = None;
    let mut task_policy: Option<PathBuf> = None;
    let mut index = 2;
    while index < args.len() {
        let flag = &args[index];
        let value = args.get(index + 1).cloned().unwrap_or_else(|| usage());
        match flag.as_str() {
            "--access" if access_policy.is_none() => access_policy = Some(value.into()),
            "--debug" if debug_policy.is_none() => debug_policy = Some(value.into()),
            "--tasks" if task_policy.is_none() => task_policy = Some(value.into()),
            _ => usage(),
        }
        index += 2;
    }
    let mut workspace = Workspace::new(root.as_str())?;
    if let Some(path) = access_policy {
        workspace.configure_access(&path)?;
    } else {
        workspace.configure_persistent_access()?;
    }
    let mut runtime = Resident::open(workspace)?;
    if let Some(path) = debug_policy {
        runtime.configure_debug(fff_workspace::debug::Configurations::load(&path)?);
    }
    if let Some(path) = task_policy {
        runtime.configure_tasks(fff_workspace::workflow::Tasks::load(&path)?);
    }
    let stdin = std::io::stdin();
    let mut input = stdin.lock();
    let stdout = std::io::stdout();
    let mut output = stdout.lock();
    loop {
        let mut bytes = Vec::new();
        let mut oversized = false;
        loop {
            let chunk = std::io::BufRead::fill_buf(&mut input)?;
            if chunk.is_empty() {
                break;
            }
            let end = chunk.iter().position(|b| *b == b'\n');
            let used = end.map_or(chunk.len(), |n| n + 1);
            if bytes.len() + used <= 1024 * 1024 && !oversized {
                bytes.extend_from_slice(&chunk[..used]);
            } else {
                oversized = true;
            }
            std::io::BufRead::consume(&mut input, used);
            if end.is_some() {
                break;
            }
        }
        if bytes.is_empty() && !oversized {
            break;
        }
        let response = if oversized {
            json!({"ok": false, "error": "request exceeds 1 MiB"})
        } else {
            match serde_json::from_slice::<Request>(&bytes) {
                Ok(request) => match runtime.execute(request) {
                    Ok(result) => json!({"ok": true, "result": result}),
                    Err(error) => json!({"ok": false, "error": error.to_string()}),
                },
                Err(error) => json!({"ok": false, "error": error.to_string()}),
            }
        };
        serde_json::to_writer(&mut output, &response)?;
        writeln!(output)?;
        std::io::Write::flush(&mut output)?;
    }
    std::process::exit(0)
}

fn is_preview(value: &Value) -> bool {
    value.get("op").and_then(Value::as_str).is_some_and(|op| {
        matches!(
            op,
            "preview_edit"
                | "preview_files"
                | "preview_create"
                | "preview_copy"
                | "preview_move"
                | "preview_delete"
                | "preview_rename"
                | "preview_semantic_rename"
        )
    })
}

const SEMANTIC_RENAME_OP: &str = "preview_semantic_rename";

fn semantic_rename_preview(runtime: &mut Resident, value: &mut Value) -> Result<Value> {
    let request: SemanticRequest = serde_json::from_value(
        value
            .get("request")
            .cloned()
            .ok_or("preview_semantic_rename requires a semantic request")?,
    )?;
    if !matches!(request.query, SemanticQuery::Rename) {
        return Err("preview_semantic_rename requires query=rename".into());
    }
    let discovery = runtime.execute(Request::Semantic { request })?;
    let hashes = discovery["file_hashes"].clone();
    if let Some(stored) = value.get("expected_file_hashes") {
        if stored != &hashes {
            return Err("analysis sources changed since preview; preview again".into());
        }
    } else {
        value["expected_file_hashes"] = hashes;
    }
    let prepared = discovery
        .get("prepared")
        .filter(|prepared| !prepared.is_null())
        .ok_or("native rename unavailable at this position")?;
    let proposal = &discovery["proposal"];
    let expected_path = match value["expected_path"].as_str() {
        Some(path) => path.to_string(),
        None => discovery["path"]
            .as_str()
            .ok_or("rename proposal has no path")?
            .to_string(),
    };
    let expected_start = match value["expected_start_byte"].as_u64() {
        Some(start) => start as usize,
        None => prepared["start_byte"]
            .as_u64()
            .ok_or("rename preparation has no start byte")? as usize,
    };
    let expected_old = match value["expected_old_text"].as_str() {
        Some(old) => old.to_string(),
        None => prepared["old_text"]
            .as_str()
            .ok_or("rename preparation has no old text")?
            .to_string(),
    };
    let expected_new = match value["expected_new_name"].as_str() {
        Some(new_name) => new_name.to_string(),
        None => discovery["new_name"]
            .as_str()
            .ok_or("rename proposal has no new name")?
            .to_string(),
    };
    let expected_edits = match value["expected_edits"].as_u64() {
        Some(edits) => edits as usize,
        None => proposal["text_edit_count"]
            .as_u64()
            .ok_or("rename proposal has no edit count")? as usize,
    };
    if expected_edits == 0 {
        return Err("preview_semantic_rename requires positive expected_edits".into());
    }
    if prepared["start_byte"].as_u64() != Some(expected_start as u64)
        || prepared["old_text"].as_str() != Some(expected_old.as_str())
    {
        return Err("rename preparation does not match the expected identity".into());
    }
    if discovery["path"].as_str() != Some(expected_path.as_str())
        || discovery["new_name"].as_str() != Some(expected_new.as_str())
    {
        return Err("rename proposal does not match the expected identity".into());
    }
    let unsupported = proposal["unsupported_reasons"]
        .as_array()
        .map(Vec::len)
        .unwrap_or(0);
    if unsupported != 0 || proposal["text_edit_count"].as_u64() != Some(expected_edits as u64) {
        return Err("rename proposal changed or needs unsupported edits; preview again".into());
    }
    let rename_set = discovery["rename_set"]
        .as_u64()
        .ok_or("rename proposal has no set handle")?;
    value["expected_path"] = json!(expected_path);
    value["expected_start_byte"] = json!(expected_start);
    value["expected_old_text"] = json!(expected_old);
    value["expected_new_name"] = json!(expected_new);
    value["expected_edits"] = json!(expected_edits);
    let mut result = runtime.execute(Request::PreviewRustRename {
        rename_set,
        expected_path,
        expected_start_byte: expected_start,
        expected_old_text: expected_old,
        expected_new_name: expected_new,
        expected_edits,
    })?;
    result["expected_edits"] = json!(expected_edits);
    Ok(result)
}

fn token_directory(root: &Path) -> Result<PathBuf> {
    let base = root.join(".ffsake");
    match fs::symlink_metadata(&base) {
        Ok(metadata) if metadata.file_type().is_symlink() || !metadata.is_dir() => {
            return Err("unsafe .ffsake directory".into());
        }
        Ok(_) => {}
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
            fs::create_dir(&base)?;
            restrict(&base, 0o700)?;
        }
        Err(error) => return Err(error.into()),
    }
    let directory = base.join("cli");
    match fs::symlink_metadata(&directory) {
        Ok(metadata) if metadata.file_type().is_symlink() || !metadata.is_dir() => {
            return Err("unsafe CLI token directory".into());
        }
        Ok(_) => {}
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
            fs::create_dir(&directory)?;
            restrict(&directory, 0o700)?;
        }
        Err(error) => return Err(error.into()),
    }
    Ok(directory)
}

fn restrict(path: &Path, mode: u32) -> Result<()> {
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        fs::set_permissions(path, fs::Permissions::from_mode(mode))?;
    }
    #[cfg(not(unix))]
    let _ = (path, mode);
    Ok(())
}

fn store_token(root: &Path, request: &str) -> Result<String> {
    if request.len() > TOKEN_MAX_BYTES {
        return Err("preview request exceeds the token size limit".into());
    }
    let directory = token_directory(root)?;
    let created = SystemTime::now().duration_since(UNIX_EPOCH)?.as_secs();
    let payload = json!({
        "version": TOKEN_VERSION,
        "root": root.to_string_lossy(),
        "created": created,
        "request": serde_json::from_str::<Value>(request)?,
    });
    let encoded = serde_json::to_vec(&payload)?;
    if encoded.len() > TOKEN_MAX_BYTES {
        return Err("encoded preview token exceeds the size limit".into());
    }
    for attempt in 0..16u32 {
        let name = format!("{:x}-{:x}-{:x}", created, std::process::id(), attempt);
        let path = directory.join(&name);
        let mut options = OpenOptions::new();
        options.write(true).create_new(true);
        #[cfg(unix)]
        {
            use std::os::unix::fs::OpenOptionsExt;
            options.mode(0o600);
        }
        match options.open(&path) {
            Ok(mut file) => {
                file.write_all(&encoded)?;
                file.sync_all()?;
                return Ok(name);
            }
            Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => continue,
            Err(error) => return Err(error.into()),
        }
    }
    Err("could not allocate a unique preview token".into())
}

fn load_token(root: &Path, token: &str) -> Result<String> {
    if token.is_empty()
        || token.len() > 128
        || !token.bytes().all(|b| b.is_ascii_hexdigit() || b == b'-')
    {
        return Err("invalid token name".into());
    }
    let directory = token_directory(root)?;
    let path = directory.join(token);
    let metadata = fs::symlink_metadata(&path)?;
    if metadata.file_type().is_symlink() || !metadata.is_file() {
        return Err("unsafe token file".into());
    }
    if metadata.len() as usize > TOKEN_MAX_BYTES {
        return Err("token exceeds the size limit".into());
    }
    let value: Value = serde_json::from_slice(&fs::read(&path)?)?;
    if value["version"].as_u64() != Some(TOKEN_VERSION) {
        return Err("unsupported token version".into());
    }
    if value["root"].as_str() != Some(root.to_string_lossy().as_ref()) {
        return Err("token belongs to a different workspace".into());
    }
    let created = value["created"].as_u64().ok_or("invalid token timestamp")?;
    let now = SystemTime::now().duration_since(UNIX_EPOCH)?.as_secs();
    if now.saturating_sub(created) > TOKEN_MAX_AGE_SECONDS {
        return Err("token expired; preview again".into());
    }
    Ok(serde_json::to_string(&value["request"])?)
}

fn remove_token(root: &Path, token: &str) -> Result<()> {
    fs::remove_file(token_directory(root)?.join(token))?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::is_preview;
    use serde_json::json;

    #[test]
    fn preview_requests_are_tokenized() {
        for op in [
            "preview_edit",
            "preview_files",
            "preview_create",
            "preview_copy",
            "preview_move",
            "preview_delete",
            "preview_rename",
            "preview_semantic_rename",
        ] {
            assert!(is_preview(&json!({"op": op})), "{op}");
        }
        for op in ["read", "apply", "undo", "redo", "debug_probe"] {
            assert!(!is_preview(&json!({"op": op})), "{op}");
        }
    }
}
