//! Read-only Rust navigation over an explicit, disposable source snapshot.
use crate::Workspace;
use crate::coordinates::{
    TextPosition, TextRange, Utf16Position, from_utf16_position, to_byte_offset, to_utf16_position,
};
pub use crate::lsp_project::{CrateSpec, RustContext};
use crate::lsp_transport::Client;
use crate::search::SearchRange;
use serde_json::{Value, json};
use std::collections::BTreeMap;
use std::path::Path;
use std::process::Command;
use std::time::{Duration, Instant};
use url::Url;

type Result<T> = std::result::Result<T, Box<dyn std::error::Error>>;

fn uri(path: &Path) -> Result<String> {
    Ok(Url::from_file_path(path)
        .map_err(|_| "invalid local file URI")?
        .into())
}

/// No mutation capability is returned. Results describe only this explicit
/// source snapshot and optional declared local crate context, not a complete Cargo build configuration.
pub fn rust_references<'a>(
    workspace: &Workspace,
    files: &[String],
    crate_root: &str,
    path: &str,
    position: TextPosition,
    context: impl Into<RustContext<'a>>,
    limit: usize,
) -> Result<Value> {
    let context = context.into();
    navigate(
        workspace,
        Query {
            files,
            crate_root,
            path,
            position,
            edition: context.edition,
            crates: context.crates,
            limit,
        },
        Navigation::References,
    )
}

/// Inspect definition, references and hover in one fresh bounded snapshot.
pub fn rust_inspect<'a>(
    workspace: &Workspace,
    files: &[String],
    crate_root: &str,
    path: &str,
    position: TextPosition,
    context: impl Into<RustContext<'a>>,
    limit: usize,
) -> Result<Value> {
    let context = context.into();
    navigate(
        workspace,
        Query {
            files,
            crate_root,
            path,
            position,
            edition: context.edition,
            crates: context.crates,
            limit,
        },
        Navigation::Inspect,
    )
}

/// Find definition locations under the same explicit snapshot contract.
pub fn rust_definition<'a>(
    workspace: &Workspace,
    files: &[String],
    crate_root: &str,
    path: &str,
    position: TextPosition,
    context: impl Into<RustContext<'a>>,
    limit: usize,
) -> Result<Value> {
    let context = context.into();
    navigate(
        workspace,
        Query {
            files,
            crate_root,
            path,
            position,
            edition: context.edition,
            crates: context.crates,
            limit,
        },
        Navigation::Definition,
    )
}

/// Get bounded hover/type documentation under the same explicit snapshot contract.
pub fn rust_hover<'a>(
    workspace: &Workspace,
    files: &[String],
    crate_root: &str,
    path: &str,
    position: TextPosition,
    context: impl Into<RustContext<'a>>,
    max_bytes: usize,
) -> Result<Value> {
    let context = context.into();
    navigate(
        workspace,
        Query {
            files,
            crate_root,
            path,
            position,
            edition: context.edition,
            crates: context.crates,
            limit: max_bytes,
        },
        Navigation::Hover,
    )
}

/// Find implementation locations under the same explicit snapshot contract.
pub fn rust_implementation<'a>(
    workspace: &Workspace,
    files: &[String],
    crate_root: &str,
    path: &str,
    position: TextPosition,
    context: impl Into<RustContext<'a>>,
    limit: usize,
) -> Result<Value> {
    let context = context.into();
    navigate(
        workspace,
        Query {
            files,
            crate_root,
            path,
            position,
            edition: context.edition,
            crates: context.crates,
            limit,
        },
        Navigation::Implementation,
    )
}

/// Discover code actions without creating any mutation capability.
pub fn rust_code_actions<'a>(
    workspace: &Workspace,
    files: &[String],
    crate_root: &str,
    path: &str,
    range: TextRange,
    context: impl Into<RustContext<'a>>,
    limit: usize,
) -> Result<Value> {
    let context = context.into();
    navigate(
        workspace,
        Query {
            files,
            crate_root,
            path,
            position: range.start,
            edition: context.edition,
            crates: context.crates,
            limit,
        },
        Navigation::CodeActions { end: range.end },
    )
}

/// Requested identifier and source position for read-only rename discovery.
pub struct RenameTarget<'a> {
    pub position: TextPosition,
    pub new_name: &'a str,
}

/// Inspect a native rename proposal without caching it or creating a mutation capability.
pub fn rust_rename_proposal<'a>(
    workspace: &Workspace,
    files: &[String],
    crate_root: &str,
    path: &str,
    target: RenameTarget<'_>,
    context: impl Into<RustContext<'a>>,
    limit: usize,
) -> Result<Value> {
    crate::syntax::validate_name(target.new_name)?;
    let context = context.into();
    navigate(
        workspace,
        Query {
            files,
            crate_root,
            path,
            position: target.position,
            edition: context.edition,
            crates: context.crates,
            limit,
        },
        Navigation::Rename {
            new_name: target.new_name,
        },
    )
}

/// Return native primary diagnostics, not a compiler/build result.
pub fn rust_diagnostics<'a>(
    workspace: &Workspace,
    files: &[String],
    crate_root: &str,
    path: &str,
    context: impl Into<RustContext<'a>>,
    limit: usize,
) -> Result<Value> {
    let context = context.into();
    navigate(
        workspace,
        Query {
            files,
            crate_root,
            path,
            position: TextPosition { line: 1, column: 0 },
            edition: context.edition,
            crates: context.crates,
            limit,
        },
        Navigation::Diagnostics,
    )
}

/// Collect native primary diagnostics for selected files in one shared snapshot.
pub fn rust_workspace_diagnostics<'a>(
    workspace: &Workspace,
    files: &[String],
    crate_root: &str,
    paths: Option<&[String]>,
    context: impl Into<RustContext<'a>>,
    limit: usize,
) -> Result<Value> {
    let context = context.into();
    navigate(
        workspace,
        Query {
            files,
            crate_root,
            path: crate_root,
            position: TextPosition { line: 1, column: 0 },
            edition: context.edition,
            crates: context.crates,
            limit,
        },
        Navigation::WorkspaceDiagnostics { paths },
    )
}

/// Return a bounded hierarchical outline for one file in the explicit snapshot.
pub fn rust_symbols<'a>(
    workspace: &Workspace,
    files: &[String],
    crate_root: &str,
    path: &str,
    context: impl Into<RustContext<'a>>,
    limit: usize,
) -> Result<Value> {
    let context = context.into();
    navigate(
        workspace,
        Query {
            files,
            crate_root,
            path,
            position: TextPosition { line: 1, column: 0 },
            edition: context.edition,
            crates: context.crates,
            limit,
        },
        Navigation::Symbols,
    )
}

#[derive(Clone, Copy)]
pub(crate) enum Navigation<'a> {
    References,
    Inspect,
    Definition,
    Implementation,
    Symbols,
    Hover,
    Diagnostics,
    WorkspaceDiagnostics { paths: Option<&'a [String]> },
    CodeActions { end: TextPosition },
    Rename { new_name: &'a str },
}

struct Query<'a> {
    files: &'a [String],
    crate_root: &'a str,
    path: &'a str,
    position: TextPosition,
    edition: &'a str,
    crates: &'a [CrateSpec],
    limit: usize,
}

fn navigate(workspace: &Workspace, query: Query<'_>, kind: Navigation<'_>) -> Result<Value> {
    let Query {
        files,
        crate_root,
        path,
        position,
        edition,
        crates,
        limit,
    } = query;
    let maximum = if matches!(kind, Navigation::Hover) {
        65_536
    } else if matches!(kind, Navigation::CodeActions { .. }) {
        100
    } else {
        1000
    };
    if files.is_empty()
        || files.len() > crate::semantic::MAX_SOURCE_FILES
        || !(1..=maximum).contains(&limit)
        || !matches!(edition, "2015" | "2018" | "2021" | "2024")
    {
        return Err(
            format!("Rust navigation requires 1..=64 files, a result bound in 1..={maximum} and a supported edition").into(),
        );
    }
    if !files.iter().any(|p| p == crate_root) || !files.iter().any(|p| p == path) {
        return Err("crate_root and query path must be explicit members of files".into());
    }
    if let Navigation::WorkspaceDiagnostics { paths: Some(paths) } = kind
        && (paths.is_empty()
            || paths.len() > crate::semantic::MAX_SOURCE_FILES
            || paths
                .iter()
                .collect::<std::collections::BTreeSet<_>>()
                .len()
                != paths.len()
            || paths.iter().any(|path| !files.contains(path)))
    {
        return Err("diagnostic paths must be 1..=64 distinct explicit members of files".into());
    }
    let deadline = Instant::now() + Duration::from_secs(30);
    let snapshot = tempfile::tempdir()?;
    let root = snapshot.path().canonicalize()?;
    workspace
        .access
        .transaction(files.iter().map(String::as_str), false)?;
    let mut identities = BTreeMap::new();
    let mut sources = BTreeMap::new();
    let mut hashes = BTreeMap::new();
    let mut names = BTreeMap::new();
    let mut source_bytes = 0;
    for file in files {
        if file.len() > 4096 || Path::new(file).extension().is_none_or(|ext| ext != "rs") {
            return Err("Rust navigation requires .rs paths of at most 4096 bytes".into());
        }
        let (identity, bytes) = workspace.read_source_bounded(Path::new(file), 256 * 1024)?;
        let normalized = crate::semantic::snapshot_name(workspace, &identity)?;
        identities.insert(normalized.clone(), identity.clone());
        crate::semantic::account_source(&mut source_bytes, bytes.len())?;
        let source = String::from_utf8(bytes)?;
        if sources.contains_key(&normalized) {
            return Err("duplicate Rust navigation file".into());
        }
        let destination = root.join(&normalized);
        std::fs::create_dir_all(destination.parent().ok_or("invalid source parent")?)?;
        std::fs::write(destination, &source)?;
        hashes.insert(
            identity,
            blake3::hash(source.as_bytes()).to_hex().to_string(),
        );
        sources.insert(normalized.clone(), source);
        names.insert(file.as_str(), normalized);
    }
    let query = &names[path];
    let query_source = &sources[query];
    let byte = to_byte_offset(query_source, position)?;
    let position = to_utf16_position(query_source, byte)?;
    let end_position = if let Navigation::CodeActions { end } = kind {
        let end_byte = to_byte_offset(query_source, end)?;
        if byte > end_byte {
            return Err("reversed code-action selection".into());
        }
        Some(to_utf16_position(query_source, end_byte)?)
    } else {
        None
    };
    let graph = crate::lsp_project::graph(&root, &names, crate_root, edition, crates)?;
    let options = json!({
        "linkedProjects":[{"crates":graph}],
        "cargo":{"buildScripts":{"enable":false},"noDeps":true,"sysroot":null},
        "files":{"exclude":[]},"workspace":{"discoverConfig":null},
        "procMacro":{"enable":false},"checkOnSave":false,
        "diagnostics":{"enable":true,"disabled":[],"experimental":{"enable":true}},"cachePriming":{"enable":false},"numThreads":2
    });
    let mut command = Command::new("rust-analyzer");
    command.current_dir(&root).env("CARGO_NET_OFFLINE", "true");
    let mut client = Client::start(&mut command, "rust-analyzer", options.clone(), deadline)?;
    let initialized = client.request("initialize", json!({"processId":null,"rootUri":uri(&root)?,
        "capabilities":{"workspace":{"workspaceEdit":{"documentChanges":true,"resourceOperations":["create","rename","delete"]}},"general":{"positionEncodings":["utf-16"]},"textDocument":{"rename":{"prepareSupport":true},"codeAction":{"codeActionLiteralSupport":{"codeActionKind":{"valueSet":["quickfix","refactor","refactor.extract","refactor.inline","refactor.rewrite","source"]}},"isPreferredSupport":true,"disabledSupport":true,"dataSupport":false},"diagnostic":{"relatedDocumentSupport":false},"hover":{"contentFormat":["markdown","plaintext"]},"definition":{"linkSupport":false},"implementation":{"linkSupport":false},"documentSymbol":{"hierarchicalDocumentSymbolSupport":true,"symbolKind":{"valueSet":(1..=26).collect::<Vec<_>>()}}},"experimental":{"serverStatusNotification":true}},
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
    let mut prepared = Value::Null;
    let (method, query_kind) = match kind {
        Navigation::Rename { new_name } => {
            if initialized["capabilities"]["renameProvider"]["prepareProvider"] != true {
                return Err("rust-analyzer does not advertise rename preparation".into());
            }
            prepared = crate::lsp_rename::prepare(
                query_source,
                byte,
                client.request("textDocument/prepareRename", parameters.clone())?,
            )?;
            parameters["newName"] = json!(new_name);
            ("textDocument/rename", "rename_proposal")
        }
        Navigation::CodeActions { .. } => {
            parameters
                .as_object_mut()
                .ok_or("invalid action request")?
                .remove("position");
            parameters["range"] =
                json!({"start":position,"end":end_position.ok_or("missing selection end")?});
            parameters["context"] = json!({"diagnostics":[],"triggerKind":1});
            ("textDocument/codeAction", "code_actions")
        }
        Navigation::References => {
            parameters["context"] = json!({"includeDeclaration":true});
            ("textDocument/references", "references")
        }
        Navigation::Inspect => ("textDocument/definition", "inspect"),
        Navigation::Definition => ("textDocument/definition", "definition"),
        Navigation::Implementation => ("textDocument/implementation", "implementation"),
        Navigation::Hover => ("textDocument/hover", "hover"),
        Navigation::Symbols | Navigation::Diagnostics | Navigation::WorkspaceDiagnostics { .. } => {
            parameters
                .as_object_mut()
                .ok_or("invalid symbol request")?
                .remove("position");
            if matches!(kind, Navigation::WorkspaceDiagnostics { .. }) {
                ("textDocument/diagnostic", "workspace_diagnostics")
            } else if matches!(kind, Navigation::Diagnostics) {
                ("textDocument/diagnostic", "diagnostics")
            } else {
                ("textDocument/documentSymbol", "symbols")
            }
        }
    };
    if matches!(
        kind,
        Navigation::Diagnostics | Navigation::WorkspaceDiagnostics { .. }
    ) && !initialized["capabilities"]["diagnosticProvider"].is_object()
    {
        return Err("rust-analyzer does not advertise pull diagnostics".into());
    }
    if matches!(kind, Navigation::CodeActions { .. })
        && initialized["capabilities"]["codeActionProvider"] != true
        && !initialized["capabilities"]["codeActionProvider"].is_object()
    {
        return Err("rust-analyzer does not advertise code actions".into());
    }
    let result = if matches!(kind, Navigation::Inspect) {
        inspect(&mut client, &root, &sources, query, parameters, limit)?
    } else if matches!(kind, Navigation::WorkspaceDiagnostics { .. }) {
        let mut batch = crate::lsp_diagnostics::Batch::new(limit);
        for (path, source) in &sources {
            if let Navigation::WorkspaceDiagnostics { paths: Some(paths) } = kind
                && !paths
                    .iter()
                    .any(|selected| names[selected.as_str()] == *path)
            {
                continue;
            }
            let value = client.request(
                "textDocument/diagnostic",
                json!({"textDocument":{"uri":uri(&root.join(path))?}}),
            )?;
            batch.push(path, source, value)?;
        }
        batch.finish()
    } else if matches!(kind, Navigation::Rename { .. }) && prepared.is_null() {
        Value::Null
    } else {
        client.request(method, parameters)?
    };
    let mut response = if let Navigation::Rename { new_name } = kind {
        json!({"path":query,"new_name":new_name,"prepared":prepared,
            "proposal":crate::lsp_rename::proposal(&root, &sources, result, limit)?,
            "semantic_validated":false,"compiler_checked":false})
    } else if matches!(kind, Navigation::CodeActions { .. }) {
        crate::lsp_actions::actions(&root, &sources, result, limit)?
    } else if matches!(
        kind,
        Navigation::WorkspaceDiagnostics { .. } | Navigation::Inspect
    ) {
        result
    } else if matches!(kind, Navigation::Diagnostics) {
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
    response["analysis_mode"] = json!(if crates.is_empty() {
        "explicit_crate_snapshot"
    } else {
        "explicit_project_snapshot"
    });
    if !crates.is_empty() {
        response["project_crates"] = json!(crates);
    }
    response["analysis_context"] =
        json!({"crate_root":names[crate_root],"edition":edition,"crates":crates});
    response["query"] = json!(query_kind);
    response["complete"] = json!(false);
    response["limitations"] = json!([
        if crates.is_empty() {
            "No Cargo dependencies, sysroot, build scripts, procedural macros or configured features are loaded."
        } else {
            "Only explicit local crate edges, features and custom cfg flags are loaded. No Cargo discovery, unspecified dependencies, sysroot, build scripts, procedural macros or implicit feature expansion are loaded."
        },
        "Results are read-only navigation data, not a semantic rename capability."
    ]);
    if matches!(kind, Navigation::Rename { .. }) {
        response["limitations"].as_array_mut().ok_or("invalid limitations")?.push(json!(
            "Read-only rename proposal, not a mutation capability. Native acceptance does not rule out binding collisions, omitted macro references, compilation failures or changed behavior."
        ));
    }
    if matches!(
        kind,
        Navigation::Diagnostics | Navigation::WorkspaceDiagnostics { .. }
    ) {
        response["compiler_checked"] = json!(false);
        response["experimental_diagnostics"] = json!(true);
        response["limitations"].as_array_mut().ok_or("invalid limitations")?.push(json!(
            "Native primary diagnostics only. Missing dependencies or configuration can cause semantic errors; an empty result does not prove the project compiles."
        ));
    }
    crate::semantic::restore_paths(&mut response, &identities);
    Ok(response)
}

fn inspect(
    client: &mut Client,
    root: &Path,
    sources: &BTreeMap<String, String>,
    path: &str,
    parameters: Value,
    limit: usize,
) -> Result<Value> {
    let locations = normalize_locations(
        client.request("textDocument/definition", parameters.clone())?,
        Navigation::Definition,
    )?;
    // Charge raw entries so duplicates cannot expand the shared server-result budget.
    let consumed = locations.as_array().map_or(0, Vec::len);
    let definitions = scoped_ranges(root, sources, locations, limit, Navigation::Definition)?;
    let mut reference_parameters = parameters.clone();
    reference_parameters["context"] = json!({"includeDeclaration":true});
    let locations = normalize_locations(
        client.request("textDocument/references", reference_parameters)?,
        Navigation::References,
    )?;
    let references = scoped_ranges(
        root,
        sources,
        locations,
        limit - consumed,
        Navigation::References,
    )?;
    let hover = crate::lsp_hover::hover(
        path,
        &sources[path],
        client.request("textDocument/hover", parameters)?,
        8192,
    )?;
    Ok(json!({
        "definition":{"ranges":definitions,"matches":definitions.len()},
        "references":{"ranges":references,"matches":references.len()},
        "hover":hover,"location_count":definitions.len()+references.len(),"compiler_checked":false
    }))
}

pub(crate) fn normalize_locations(value: Value, kind: Navigation<'_>) -> Result<Value> {
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

pub(crate) fn snapshot_path(
    root: &Path,
    sources: &BTreeMap<String, String>,
    value: &str,
) -> Result<String> {
    let url = Url::parse(value)?;
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
    if !sources.contains_key(&path) {
        return Err("LSP result outside explicit source files".into());
    }
    Ok(path)
}

pub(crate) fn scoped_ranges(
    root: &Path,
    sources: &BTreeMap<String, String>,
    locations: Value,
    limit: usize,
    kind: Navigation<'_>,
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
        let path = snapshot_path(
            root,
            sources,
            location["uri"].as_str().ok_or("missing LSP URI")?,
        )?;
        let source = &sources[&path];
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
            vec!["main.rs".into(); crate::semantic::MAX_SOURCE_FILES + 1],
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
