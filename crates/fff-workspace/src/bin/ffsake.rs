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
         preview_delete, preview_rename) also print a token for --apply.\n\
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
            let value: Value = serde_json::from_str(&text)?;
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
            let value: Value = serde_json::from_str(&text)?;
            if !is_preview(&value) {
                return Err("token does not contain a preview request".into());
            }
            let request: Request = serde_json::from_value(value)?;
            let preview = runtime.execute(request)?;
            let handle = preview
                .get("handle")
                .and_then(Value::as_u64)
                .ok_or("preview did not return an apply handle")?;
            let result = runtime.execute(Request::Apply { handle })?;
            let _ = remove_token(&canonical_root, &token);
            Ok(json!({ "ok": true, "result": result, "token_consumed": true }))
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
        )
    })
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
        ] {
            assert!(is_preview(&json!({"op": op})), "{op}");
        }
        for op in ["read", "apply", "undo", "redo", "debug_probe"] {
            assert!(!is_preview(&json!({"op": op})), "{op}");
        }
    }
}
