//! Language-neutral requests. Adapters own project configuration, not transport or edits.
use crate::{
    Workspace,
    coordinates::TextPosition,
    lsp::{self, CrateSpec, RustContext},
};
use serde::Deserialize;
use serde_json::Value;
type Result<T> = std::result::Result<T, Box<dyn std::error::Error>>;
#[derive(Debug, Clone, Copy, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Query {
    Definition,
    References,
    Hover,
    Symbols,
    Diagnostics,
    Inspect,
    Rename,
}
#[derive(Debug, Deserialize)]
#[serde(tag = "language", rename_all = "snake_case", deny_unknown_fields)]
pub enum Context {
    Rust {
        crate_root: String,
        edition: String,
        #[serde(default)]
        crates: Vec<CrateSpec>,
    },
    Cpp {
        standard: String,
    },
}
#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Request {
    pub files: Vec<String>,
    pub path: String,
    pub position: TextPosition,
    pub query: Query,
    pub context: Context,
    pub new_name: Option<String>,
    pub limit: usize,
}
pub fn query(workspace: &Workspace, request: &Request) -> Result<Value> {
    if !(1..=1000).contains(&request.limit) {
        return Err("semantic result limit must be 1..=1000".into());
    }
    if !matches!(request.query, Query::Rename) && request.new_name.is_some() {
        return Err("new_name is only valid for rename".into());
    }
    match &request.context {
        Context::Cpp { standard } => crate::lsp_cpp::query(workspace, request, standard),
        Context::Rust {
            crate_root,
            edition,
            crates,
        } => {
            let c = RustContext { edition, crates };
            let f = &request.files;
            let p = &request.path;
            let at = request.position;
            let n = request.limit;
            match request.query {
                Query::Definition => lsp::rust_definition(workspace, f, crate_root, p, at, c, n),
                Query::References => lsp::rust_references(workspace, f, crate_root, p, at, c, n),
                Query::Hover => lsp::rust_hover(workspace, f, crate_root, p, at, c, n),
                Query::Symbols => lsp::rust_symbols(workspace, f, crate_root, p, c, n),
                Query::Diagnostics => lsp::rust_diagnostics(workspace, f, crate_root, p, c, n),
                Query::Inspect => lsp::rust_inspect(workspace, f, crate_root, p, at, c, n),
                Query::Rename => lsp::rust_rename_proposal(
                    workspace,
                    f,
                    crate_root,
                    p,
                    lsp::RenameTarget {
                        position: at,
                        new_name: request
                            .new_name
                            .as_deref()
                            .ok_or("rename requires new_name")?,
                    },
                    c,
                    n,
                ),
            }
        }
    }
}
