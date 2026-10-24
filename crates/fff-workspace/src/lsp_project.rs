//! Explicit local crate graphs, independent of Cargo discovery and execution.
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use std::collections::{BTreeMap, BTreeSet};
use std::path::Path;

type Result<T> = std::result::Result<T, Box<dyn std::error::Error>>;

#[derive(Debug, Clone, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct CrateSpec {
    pub root: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub edition: Option<String>,
    #[serde(default)]
    pub dependencies: BTreeMap<String, String>,
}

#[derive(Clone, Copy)]
pub struct RustContext<'a> {
    pub edition: &'a str,
    pub crates: &'a [CrateSpec],
}

impl<'a> From<&'a str> for RustContext<'a> {
    fn from(edition: &'a str) -> Self {
        Self {
            edition,
            crates: &[],
        }
    }
}

fn dependency_name(name: &str) -> bool {
    // Deliberately restrict the API to ordinary ASCII crate names, excluding
    // Rust keywords. Raw/Unicode dependency spellings are not accepted here.
    !name.is_empty()
        && name.len() <= 128
        && name != "_"
        && name.bytes().enumerate().all(|(index, b)| {
            b == b'_' || b.is_ascii_alphabetic() || (index > 0 && b.is_ascii_digit())
        })
        && ![
            "as", "async", "await", "break", "const", "continue", "crate", "dyn", "else", "enum",
            "extern", "false", "fn", "for", "if", "impl", "in", "let", "loop", "match", "mod",
            "move", "mut", "pub", "ref", "return", "self", "Self", "static", "struct", "super",
            "trait", "true", "type", "unsafe", "use", "where", "while", "abstract", "become",
            "box", "do", "final", "gen", "macro", "override", "priv", "try", "typeof", "unsized",
            "virtual", "yield",
        ]
        .contains(&name)
}

pub(crate) fn graph(
    snapshot: &Path,
    names: &BTreeMap<&str, String>,
    primary: &str,
    default_edition: &str,
    explicit: &[CrateSpec],
) -> Result<Vec<Value>> {
    let default = [CrateSpec {
        root: primary.into(),
        edition: None,
        dependencies: BTreeMap::new(),
    }];
    let crates = if explicit.is_empty() {
        &default[..]
    } else {
        explicit
    };
    if crates.len() > 16 {
        return Err("explicit Rust project exceeds 16 crates".into());
    }
    let mut indexes = BTreeMap::new();
    let mut roots = BTreeSet::new();
    for (index, krate) in crates.iter().enumerate() {
        let normalized = names
            .get(krate.root.as_str())
            .ok_or("crate root is not an explicit source file")?;
        if !roots.insert(normalized) || indexes.insert(krate.root.as_str(), index).is_some() {
            return Err("duplicate Rust crate root".into());
        }
        if krate.dependencies.len() > 16 {
            return Err("crate exceeds 16 dependencies".into());
        }
        if !matches!(
            krate.edition.as_deref().unwrap_or(default_edition),
            "2015" | "2018" | "2021" | "2024"
        ) {
            return Err("unsupported crate edition".into());
        }
    }
    if !indexes.contains_key(primary) {
        return Err("explicit project must include crate_root".into());
    }
    let edges: usize = crates.iter().map(|krate| krate.dependencies.len()).sum();
    if edges > 64 {
        return Err("explicit Rust project exceeds 64 dependency edges".into());
    }
    let mut graph = Vec::new();
    let mut adjacency = vec![Vec::new(); crates.len()];
    for (index, krate) in crates.iter().enumerate() {
        let mut dependencies = Vec::new();
        for (name, target) in &krate.dependencies {
            if !dependency_name(name) {
                return Err("invalid explicit Rust dependency name".into());
            }
            let dependency = *indexes
                .get(target.as_str())
                .ok_or("dependency target must be a declared crate root")?;
            adjacency[index].push(dependency);
            dependencies.push(json!({"crate":dependency,"name":name}));
        }
        graph.push(
            json!({"root_module":snapshot.join(&names[krate.root.as_str()]),
            "edition":krate.edition.as_deref().unwrap_or(default_edition),"deps":dependencies,
            "cfg":[],"env":{},"is_workspace_member":true,
            "source":{"include_dirs":[snapshot],"exclude_dirs":[]}}),
        );
    }
    fn visit(index: usize, graph: &[Vec<usize>], states: &mut [u8]) -> bool {
        if states[index] == 1 {
            return false;
        }
        if states[index] == 2 {
            return true;
        }
        states[index] = 1;
        for &dependency in &graph[index] {
            if !visit(dependency, graph, states) {
                return false;
            }
        }
        states[index] = 2;
        true
    }
    let mut states = vec![0; crates.len()];
    for index in 0..crates.len() {
        if !visit(index, &adjacency, &mut states) {
            return Err("cyclic Rust crate dependencies".into());
        }
    }
    Ok(graph)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn specs() -> Vec<CrateSpec> {
        vec![
            CrateSpec {
                root: "app.rs".into(),
                edition: None,
                dependencies: BTreeMap::from([("account_model".into(), "model.rs".into())]),
            },
            CrateSpec {
                root: "model.rs".into(),
                edition: Some("2021".into()),
                dependencies: BTreeMap::new(),
            },
        ]
    }

    #[test]
    fn explicit_graph_maps_only_declared_roots_and_dependency_names() {
        let names = BTreeMap::from([("app.rs", "app.rs".into()), ("model.rs", "model.rs".into())]);
        let root = std::env::temp_dir();
        let result = graph(&root, &names, "app.rs", "2024", &specs()).unwrap();
        assert_eq!(
            result[0]["deps"],
            json!([{"crate":1,"name":"account_model"}])
        );
        assert_eq!(result[1]["edition"], "2021");
        assert_eq!(result[1]["root_module"], json!(root.join("model.rs")));
        let single = graph(&root, &names, "app.rs", "2024", &[]).unwrap();
        assert_eq!(single.len(), 1);
        assert_eq!(single[0]["deps"], json!([]));
    }

    #[test]
    fn invalid_graphs_reject_without_starting_a_server() {
        let names = BTreeMap::from([("app.rs", "app.rs".into()), ("model.rs", "model.rs".into())]);
        let root = std::env::temp_dir();
        let valid = specs();
        let mut variants = Vec::new();
        let mut cycle = valid.clone();
        cycle[1].dependencies.insert("app".into(), "app.rs".into());
        variants.push(cycle);
        let mut self_edge = valid.clone();
        self_edge[0]
            .dependencies
            .insert("app".into(), "app.rs".into());
        variants.push(self_edge);
        let mut missing = valid.clone();
        missing[0]
            .dependencies
            .insert("missing".into(), "elsewhere.rs".into());
        variants.push(missing);
        let mut outside = valid.clone();
        outside[1].root = "../outside.rs".into();
        variants.push(outside);
        let mut duplicate = valid.clone();
        duplicate.push(valid[0].clone());
        variants.push(duplicate);
        variants.push(vec![valid[1].clone()]);
        let mut edition = valid.clone();
        edition[1].edition = Some("2030".into());
        variants.push(edition);
        for name in [
            "self",
            "type",
            "gen",
            "_",
            "1bad",
            "hyphen-name",
            "r#type",
            "café",
            &"x".repeat(129),
        ] {
            let mut invalid = valid.clone();
            invalid[0].dependencies = BTreeMap::from([(name.into(), "model.rs".into())]);
            variants.push(invalid);
        }
        let mut many = valid.clone();
        many[0].dependencies = (0..17)
            .map(|n| (format!("name{n}"), "model.rs".into()))
            .collect();
        variants.push(many);
        variants.push(vec![valid[0].clone(); 17]);
        for invalid in variants {
            assert!(graph(&root, &names, "app.rs", "2024", &invalid).is_err());
        }
    }
}
