use crate::{FileSelector, PathSelector, ReplaceMode};

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ExecutionPlan {
    pub selector: FileSelector,
    pub operations: Vec<Operation>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Operation {
    Read,
    Delete,
    Replace {
        mode: ReplaceMode,
        pattern: String,
        replacement: String,
    },
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ParseError {
    Empty,
    MissingOperation,
    InvalidTarget(String),
    InvalidOperation(String),
}

impl std::fmt::Display for ParseError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Empty => write!(f, "empty command"),
            Self::MissingOperation => write!(f, "command must contain at least one operation"),
            Self::InvalidTarget(value) => write!(f, "invalid target: {value}"),
            Self::InvalidOperation(value) => write!(f, "invalid operation: {value}"),
        }
    }
}

impl std::error::Error for ParseError {}

pub fn parse(input: &str) -> Result<ExecutionPlan, ParseError> {
    let parts = split_pipeline(input)?;
    if parts.is_empty() {
        return Err(ParseError::Empty);
    }
    if parts.len() < 2 {
        return Err(ParseError::MissingOperation);
    }

    let selector = parse_target(parts[0].trim())?;
    let mut operations = Vec::with_capacity(parts.len() - 1);
    for part in &parts[1..] {
        operations.push(parse_operation(part.trim())?);
    }

    Ok(ExecutionPlan {
        selector,
        operations,
    })
}

fn parse_target(input: &str) -> Result<FileSelector, ParseError> {
    if input == "*" {
        return Ok(FileSelector::all());
    }

    let body = if input.starts_with('[') && input.ends_with(']') {
        &input[1..input.len() - 1]
    } else {
        input
    };

    let mut selector = FileSelector::default();
    for raw in split_top_level(body, ',')? {
        let raw = raw.trim();
        if raw.is_empty() {
            continue;
        }

        let (exclude, token) = raw
            .strip_prefix('!')
            .map_or((false, raw), |rest| (true, rest.trim()));

        let parsed = parse_selector_token(token)?;
        if exclude {
            selector.exclude.push(parsed);
        } else {
            selector.include.push(parsed);
        }
    }

    if selector.include.is_empty() {
        return Err(ParseError::InvalidTarget(input.to_string()));
    }

    Ok(selector)
}

fn parse_selector_token(input: &str) -> Result<PathSelector, ParseError> {
    if input == "*" {
        return Ok(PathSelector::All);
    }
    if let Some(path) = input.strip_prefix("exact:") {
        return nonempty(path, input).map(|v| PathSelector::Exact(v.to_string()));
    }
    if let Some(pattern) = input.strip_prefix("glob:") {
        return nonempty(pattern, input).map(|v| PathSelector::Glob(v.to_string()));
    }
    if let Some(pattern) = input.strip_prefix("re:") {
        return nonempty(pattern, input).map(|v| PathSelector::Regex(v.to_string()));
    }

    // Bare paths are exact by default. Pattern syntax must be explicit.
    if input.contains(':') {
        return Err(ParseError::InvalidTarget(input.to_string()));
    }
    nonempty(input, input).map(|v| PathSelector::Exact(v.to_string()))
}

fn parse_operation(input: &str) -> Result<Operation, ParseError> {
    match input {
        "read" => return Ok(Operation::Read),
        "delete" => return Ok(Operation::Delete),
        _ => {}
    }

    let Some(rest) = input.strip_prefix("replace ") else {
        return Err(ParseError::InvalidOperation(input.to_string()));
    };
    let Some((lhs, rhs)) = rest.split_once("=>") else {
        return Err(ParseError::InvalidOperation(input.to_string()));
    };

    let lhs = lhs.trim();
    let replacement = rhs.trim().to_string();

    if let Some(pattern) = lhs.strip_prefix("literal:") {
        let pattern = nonempty(pattern.trim(), input)?;
        return Ok(Operation::Replace {
            mode: ReplaceMode::Literal,
            pattern: pattern.to_string(),
            replacement,
        });
    }

    if let Some(pattern) = lhs.strip_prefix("re:") {
        let pattern = nonempty(pattern.trim(), input)?;
        return Ok(Operation::Replace {
            mode: ReplaceMode::Regex,
            pattern: pattern.to_string(),
            replacement,
        });
    }

    Err(ParseError::InvalidOperation(input.to_string()))
}

fn nonempty<'a>(value: &'a str, original: &str) -> Result<&'a str, ParseError> {
    let value = value.trim();
    if value.is_empty() {
        Err(ParseError::InvalidTarget(original.to_string()))
    } else {
        Ok(value)
    }
}

fn split_pipeline(input: &str) -> Result<Vec<String>, ParseError> {
    split_top_level(input, '|')
}

fn split_top_level(input: &str, delimiter: char) -> Result<Vec<String>, ParseError> {
    let mut out = Vec::new();
    let mut current = String::new();
    let mut bracket_depth = 0usize;
    let mut quote: Option<char> = None;
    let mut escaped = false;

    for ch in input.chars() {
        if escaped {
            current.push(ch);
            escaped = false;
            continue;
        }

        if ch == '\\' {
            current.push(ch);
            escaped = true;
            continue;
        }

        if let Some(q) = quote {
            current.push(ch);
            if ch == q {
                quote = None;
            }
            continue;
        }

        if ch == '"' || ch == '\'' {
            current.push(ch);
            quote = Some(ch);
            continue;
        }

        match ch {
            '[' => {
                bracket_depth += 1;
                current.push(ch);
            }
            ']' => {
                if bracket_depth == 0 {
                    return Err(ParseError::InvalidTarget(input.to_string()));
                }
                bracket_depth -= 1;
                current.push(ch);
            }
            _ if ch == delimiter && bracket_depth == 0 => {
                out.push(current.trim().to_string());
                current.clear();
            }
            _ => current.push(ch),
        }
    }

    if bracket_depth != 0 || quote.is_some() {
        return Err(ParseError::InvalidTarget(input.to_string()));
    }

    if !current.trim().is_empty() {
        out.push(current.trim().to_string());
    }

    Ok(out)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_all_files_and_replace() {
        let plan = parse("* | replace literal:Foo => Bar").unwrap();
        assert_eq!(plan.selector, FileSelector::all());
        assert_eq!(
            plan.operations,
            vec![Operation::Replace {
                mode: ReplaceMode::Literal,
                pattern: "Foo".into(),
                replacement: "Bar".into(),
            }]
        );
    }

    #[test]
    fn parses_union_and_exclusion() {
        let plan = parse("[src/lib.rs, re:^src/.*\\.rs$, !glob:**/generated/**] | read").unwrap();

        assert_eq!(
            plan.selector.include,
            vec![
                PathSelector::Exact("src/lib.rs".into()),
                PathSelector::Regex(r"^src/.*\.rs$".into()),
            ]
        );
        assert_eq!(
            plan.selector.exclude,
            vec![PathSelector::Glob("**/generated/**".into())]
        );
        assert_eq!(plan.operations, vec![Operation::Read]);
    }

    #[test]
    fn parses_pipeline() {
        let plan = parse("glob:**/*.rs | replace re:\\bUser\\b => Customer | read").unwrap();
        assert_eq!(plan.operations.len(), 2);
    }

    #[test]
    fn bare_target_is_exact() {
        let plan = parse("README.md | read").unwrap();
        assert_eq!(plan.selector, FileSelector::exact("README.md"));
    }
}
