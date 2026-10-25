//! Bounded read-only rename proposals; native acceptance is not semantic proof.
use crate::coordinates::{Utf16Position, from_utf16_position};
use serde_json::{Value, json};
use std::collections::BTreeMap;
use std::path::Path;

type Result<T> = std::result::Result<T, Box<dyn std::error::Error>>;

pub(crate) fn prepare(source: &str, cursor: usize, value: Value) -> Result<Value> {
    if value.is_null() {
        return Ok(Value::Null);
    }
    if !value.is_object() || value.get("defaultBehavior").is_some() {
        return Err("invalid or unsupported prepare-rename result".into());
    }
    let range = if let Some(range) = value.get("range") {
        let placeholder = value["placeholder"]
            .as_str()
            .ok_or("missing rename placeholder")?;
        if placeholder.is_empty() || placeholder.len() > 256 {
            return Err("rename placeholder exceeds name bound".into());
        }
        range
    } else {
        &value
    };
    let start: Utf16Position = serde_json::from_value(range["start"].clone())?;
    let end: Utf16Position = serde_json::from_value(range["end"].clone())?;
    let start = from_utf16_position(source, start)?;
    let end = from_utf16_position(source, end)?;
    if start >= end || start > cursor || cursor > end || end - start > 256 {
        return Err("invalid rename selection range".into());
    }
    Ok(json!({"start_byte":start,"end_byte":end,"old_text":&source[start..end]}))
}

pub(crate) fn proposal(
    root: &Path,
    sources: &BTreeMap<String, String>,
    value: Value,
    limit: usize,
) -> Result<Value> {
    if value.is_null() {
        return Ok(Value::Null);
    }
    // Reuse the complete workspace-edit classifier, including all-or-nothing
    // resource/annotation handling, exact ranges, overlaps and output budgets.
    let normalized = crate::lsp_actions::actions(
        root,
        sources,
        json!([{"title":"Rename proposal","edit":value}]),
        1,
    )?;
    let action = &normalized["actions"][0];
    if action["text_edit_count"]
        .as_u64()
        .ok_or("missing rename edit count")?
        > limit as u64
    {
        return Err("rename edit limit exceeded; no partial result returned".into());
    }
    Ok(
        json!({"edits":action["edits"],"text_edit_count":action["text_edit_count"],
        "unsupported_reasons":action["unsupported_reasons"]}),
    )
}

pub(crate) struct Expectation<'a> {
    pub path: &'a str,
    pub start_byte: usize,
    pub old_text: &'a str,
    pub new_name: &'a str,
    pub edits: usize,
}

/// Only a resident-owned report reaches this function; callers cannot supply edits.
pub(crate) fn preview_transform(
    workspace: &crate::Workspace,
    report: &Value,
    expected: Expectation<'_>,
) -> Result<crate::transform::Transform> {
    if report["prepared"].is_null()
        || report["path"] != expected.path
        || report["prepared"]["start_byte"].as_u64() != Some(expected.start_byte as u64)
        || report["prepared"]["old_text"] != expected.old_text
        || report["new_name"] != expected.new_name
    {
        return Err("rename selection or name differs from expected identity".into());
    }
    if report["proposal"].is_null() {
        return Err("rename has no proposal".into());
    }
    let mut action = report["proposal"].clone();
    action["title"] = json!("Rename proposal");
    let adapted = json!({"language":report["language"],"file_hashes":report["file_hashes"],"actions":[action]});
    crate::lsp_actions::preview_transform(workspace, &adapted, 0, "Rename proposal", expected.edits)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn prepared_selection_is_bounded_and_contains_original_cursor() {
        let source = "// 🚀\r\nlet account = 1;";
        let range = json!({"start":{"line":1,"character":4},"end":{"line":1,"character":11}});
        let cursor = source.find("account").unwrap();
        let expected = prepare(source, cursor, range.clone()).unwrap();
        assert_eq!(expected["old_text"], "account");
        assert_eq!(expected["start_byte"], cursor);
        assert_eq!(
            prepare(
                source,
                cursor,
                json!({"range":range,"placeholder":"account"})
            )
            .unwrap(),
            expected
        );
        assert!(prepare(source, 0, range.clone()).is_err());
        assert!(prepare(source, cursor, json!({"range":range})).is_err());
        assert!(prepare(source, cursor, json!({"defaultBehavior":true})).is_err());
        assert!(
            prepare(
                source,
                cursor,
                json!({"start":{"line":0,"character":4},"end":{"line":1,"character":11}})
            )
            .is_err()
        );
        assert!(prepare(source, cursor, Value::Null).unwrap().is_null());
    }

    #[test]
    fn rename_proposals_keep_workspace_edit_scope_and_whole_operation_guards() {
        let root = tempfile::tempdir().unwrap();
        let root = root.path().canonicalize().unwrap();
        let sources = BTreeMap::from([("main.rs".into(), "let account = account;".into())]);
        let uri = url::Url::from_file_path(root.join("main.rs"))
            .unwrap()
            .to_string();
        let edit = json!({"range":{"start":{"line":0,"character":4},"end":{"line":0,"character":11}},"newText":"customer"});
        let value = json!({"changes":{uri.clone():[edit.clone()]}});
        assert_eq!(
            proposal(&root, &sources, value.clone(), 1).unwrap()["text_edit_count"],
            1
        );
        assert!(proposal(&root, &sources, value, 0).is_err());
        let unsupported = proposal(
            &root,
            &sources,
            json!({"documentChanges":[
            {"textDocument":{"uri":uri,"version":1},"edits":[edit.clone()]},
            {"kind":"rename","oldUri":"file:///outside.rs","newUri":"file:///other.rs"}]}),
            10,
        )
        .unwrap();
        assert!(unsupported["edits"].is_null());
        assert_eq!(
            unsupported["unsupported_reasons"],
            json!(["resource_operation"])
        );
        assert!(
            proposal(
                &root,
                &sources,
                json!({"changes":{"file:///outside.rs":[edit]}}),
                10
            )
            .is_err()
        );
        assert!(
            proposal(&root, &sources, Value::Null, 10)
                .unwrap()
                .is_null()
        );
    }
}
