//! JSON the way the Python helper wrote it (`json.dumps`, `ensure_ascii`,
//! separators `", "` and `": "`, or `indent=2`), and the Python semantics the
//! helper relied on: `str()`, truthiness and `==` of decoded JSON values.

use serde_json::{Number, Value};

/// `json.loads` of bytes: an optional UTF-8 BOM, then one JSON value.
pub fn loads(raw: &[u8]) -> Result<Value, String> {
    let raw = raw.strip_prefix(b"\xef\xbb\xbf").unwrap_or(raw);
    serde_json::from_slice(raw).map_err(|error| error.to_string())
}

/// `json.loads` of text read with `read_text()`: no BOM is skipped.
pub fn loads_text(raw: &[u8]) -> Result<Value, String> {
    let text = std::str::from_utf8(raw).map_err(|error| error.to_string())?;
    if text.starts_with('\u{feff}') {
        return Err("Unexpected UTF-8 BOM (decode using utf-8-sig)".into());
    }
    serde_json::from_str(text).map_err(|error| error.to_string())
}

/// `json.dumps(value)`.
pub fn dumps(value: &Value) -> String {
    let mut out = String::new();
    write_value(&mut out, value, None, 0);
    out
}

/// `json.dumps(value, indent=indent)`.
pub fn dumps_indent(value: &Value, indent: usize) -> String {
    let mut out = String::new();
    write_value(&mut out, value, Some(indent), 0);
    out
}

fn newline(out: &mut String, indent: Option<usize>, level: usize) {
    if let Some(width) = indent {
        out.push('\n');
        out.extend(std::iter::repeat_n(' ', width * level));
    }
}

fn write_value(out: &mut String, value: &Value, indent: Option<usize>, level: usize) {
    let separator = if indent.is_some() { "," } else { ", " };
    match value {
        Value::Null => out.push_str("null"),
        Value::Bool(true) => out.push_str("true"),
        Value::Bool(false) => out.push_str("false"),
        Value::Number(number) => out.push_str(&number_repr(number)),
        Value::String(text) => write_str(out, text),
        Value::Array(items) if items.is_empty() => out.push_str("[]"),
        Value::Array(items) => {
            out.push('[');
            for (i, item) in items.iter().enumerate() {
                if i > 0 {
                    out.push_str(separator);
                }
                newline(out, indent, level + 1);
                write_value(out, item, indent, level + 1);
            }
            newline(out, indent, level);
            out.push(']');
        }
        Value::Object(map) if map.is_empty() => out.push_str("{}"),
        Value::Object(map) => {
            out.push('{');
            for (i, (key, item)) in map.iter().enumerate() {
                if i > 0 {
                    out.push_str(separator);
                }
                newline(out, indent, level + 1);
                write_str(out, key);
                out.push_str(": ");
                write_value(out, item, indent, level + 1);
            }
            newline(out, indent, level);
            out.push('}');
        }
    }
}

/// A JSON string with every character outside `' '..='~'` escaped.
fn write_str(out: &mut String, text: &str) {
    out.push('"');
    for c in text.chars() {
        match c {
            '\\' => out.push_str("\\\\"),
            '"' => out.push_str("\\\""),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            '\u{8}' => out.push_str("\\b"),
            '\u{c}' => out.push_str("\\f"),
            ' '..='~' => out.push(c),
            _ => {
                let mut units = [0u16; 2];
                for unit in c.encode_utf16(&mut units) {
                    out.push_str(&format!("\\u{unit:04x}"));
                }
            }
        }
    }
    out.push('"');
}

/// `repr()` of the number Python's `json.loads` would have made of it.
pub fn number_repr(number: &Number) -> String {
    if let Some(value) = number.as_u64() {
        value.to_string()
    } else if let Some(value) = number.as_i64() {
        value.to_string()
    } else {
        float_repr(number.as_f64().unwrap_or(f64::NAN))
    }
}

/// Python's `repr(float)`: the shortest round-trip digits, in exponent
/// notation below 1e-4 and from 1e16 on.
pub fn float_repr(value: f64) -> String {
    if value.is_nan() {
        return "NaN".into();
    }
    if value.is_infinite() {
        return if value > 0.0 { "Infinity" } else { "-Infinity" }.into();
    }
    let scientific = format!("{value:e}");
    let (mantissa, exponent) = scientific.split_once('e').unwrap_or((&scientific, "0"));
    let exponent: i32 = exponent.parse().unwrap_or(0);
    let (sign, mantissa) = match mantissa.strip_prefix('-') {
        Some(rest) => ("-", rest),
        None => ("", mantissa),
    };
    let digits: String = mantissa.chars().filter(|c| *c != '.').collect();
    let point = exponent + 1;
    if !(-4 < point && point <= 16) {
        let (first, rest) = digits.split_at(1);
        let rest = if rest.is_empty() {
            String::new()
        } else {
            format!(".{rest}")
        };
        let exp_sign = if exponent < 0 { '-' } else { '+' };
        return format!("{sign}{first}{rest}e{exp_sign}{:02}", exponent.abs());
    }
    let count = digits.len() as i32;
    if point <= 0 {
        format!("{sign}0.{}{digits}", "0".repeat((-point) as usize))
    } else if point < count {
        let (whole, fraction) = digits.split_at(point as usize);
        format!("{sign}{whole}.{fraction}")
    } else {
        format!("{sign}{digits}{}.0", "0".repeat((point - count) as usize))
    }
}

/// Python's `repr(str)`.
fn str_repr(text: &str) -> String {
    let quote = if text.contains('\'') && !text.contains('"') {
        '"'
    } else {
        '\''
    };
    let mut out = String::from(quote);
    for c in text.chars() {
        match c {
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            c if c == quote => {
                out.push('\\');
                out.push(c);
            }
            c if (c as u32) < 0x20 || c as u32 == 0x7f => {
                out.push_str(&format!("\\x{:02x}", c as u32))
            }
            c => out.push(c),
        }
    }
    out.push(quote);
    out
}

/// Python's `repr()` of a decoded JSON value.
pub fn repr(value: &Value) -> String {
    match value {
        Value::String(text) => str_repr(text),
        Value::Array(items) => format!(
            "[{}]",
            items.iter().map(repr).collect::<Vec<_>>().join(", ")
        ),
        Value::Object(map) => format!(
            "{{{}}}",
            map.iter()
                .map(|(key, item)| format!("{}: {}", str_repr(key), repr(item)))
                .collect::<Vec<_>>()
                .join(", ")
        ),
        other => py_str(other),
    }
}

/// Python's `str()` of a decoded JSON value.
pub fn py_str(value: &Value) -> String {
    match value {
        Value::Null => "None".into(),
        Value::Bool(true) => "True".into(),
        Value::Bool(false) => "False".into(),
        Value::Number(number) => number_repr(number),
        Value::String(text) => text.clone(),
        other => repr(other),
    }
}

/// Python's truthiness of a decoded JSON value.
pub fn truthy(value: &Value) -> bool {
    match value {
        Value::Null => false,
        Value::Bool(b) => *b,
        Value::Number(number) => number.as_f64().is_some_and(|n| n != 0.0),
        Value::String(text) => !text.is_empty(),
        Value::Array(items) => !items.is_empty(),
        Value::Object(map) => !map.is_empty(),
    }
}

/// A number or bool as Python compares it (`True == 1`, `1 == 1.0`).
pub fn numeric(value: &Value) -> Option<f64> {
    match value {
        Value::Bool(b) => Some(f64::from(u8::from(*b))),
        Value::Number(number) => number.as_f64(),
        _ => None,
    }
}

fn integer(value: &Value) -> Option<i128> {
    match value {
        Value::Bool(b) => Some(i128::from(*b)),
        Value::Number(number) => number
            .as_i64()
            .map(i128::from)
            .or_else(|| number.as_u64().map(i128::from)),
        _ => None,
    }
}

/// Python's `==` between decoded JSON values.
pub fn py_eq(a: &Value, b: &Value) -> bool {
    match (a, b) {
        (Value::Null, Value::Null) => true,
        (Value::String(x), Value::String(y)) => x == y,
        (Value::Array(x), Value::Array(y)) => {
            x.len() == y.len() && x.iter().zip(y).all(|(x, y)| py_eq(x, y))
        }
        (Value::Object(x), Value::Object(y)) => {
            x.len() == y.len()
                && x.iter()
                    .all(|(key, value)| y.get(key).is_some_and(|other| py_eq(value, other)))
        }
        _ => match (integer(a), integer(b)) {
            (Some(x), Some(y)) => x == y,
            _ => matches!((numeric(a), numeric(b)), (Some(x), Some(y)) if x == y),
        },
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn floats_print_like_python_repr() {
        let cases = [
            (0.0, "0.0"),
            (-0.0, "-0.0"),
            (0.5, "0.5"),
            (3.0, "3.0"),
            (1e16, "1e+16"),
            (1.5e16, "1.5e+16"),
            (1234567890123456.0, "1234567890123456.0"),
            (0.0001, "0.0001"),
            (1e-5, "1e-05"),
            (-2.5e-7, "-2.5e-07"),
            (1e100, "1e+100"),
            (0.1 + 0.2, "0.30000000000000004"),
            (123.456, "123.456"),
        ];
        for (value, text) in cases {
            assert_eq!(float_repr(value), text, "{value}");
        }
    }

    #[test]
    fn dumps_matches_python() {
        let value: Value = loads(br#"{"b": 1, "a": [1.0, true, null, "x\u00e5\ud83d\ude00\u007f\u0001\t\"\\/"], "e": {}, "l": []}"#).unwrap();
        assert_eq!(
            dumps(&value),
            r#"{"b": 1, "a": [1.0, true, null, "x\u00e5\ud83d\ude00\u007f\u0001\t\"\\/"], "e": {}, "l": []}"#
        );
        assert_eq!(
            dumps_indent(
                &json!({"version": 5, "dnd": false, "snoozes": {}, "x": [1, {"y": 2}]}),
                2
            ),
            "{\n  \"version\": 5,\n  \"dnd\": false,\n  \"snoozes\": {},\n  \"x\": [\n    1,\n    {\n      \"y\": 2\n    }\n  ]\n}"
        );
    }

    #[test]
    fn python_values() {
        assert_eq!(py_str(&json!(null)), "None");
        assert_eq!(py_str(&json!(true)), "True");
        assert_eq!(py_str(&json!(1.0)), "1.0");
        assert_eq!(py_str(&json!(-3)), "-3");
        assert_eq!(py_str(&json!(["a'b", {"k": 1}])), "[\"a'b\", {'k': 1}]");
        assert!(py_eq(&json!(1), &json!(1.0)));
        assert!(py_eq(&json!(true), &json!(1)));
        assert!(py_eq(&json!({"a": 1, "b": 2}), &json!({"b": 2, "a": 1})));
        assert!(!py_eq(&json!("1"), &json!(1)));
        assert!(!truthy(&json!(0.0)));
        assert!(truthy(&json!(" ")));
        assert!(loads(b"\xef\xbb\xbf{}").is_ok());
        assert!(loads_text(b"\xef\xbb\xbf{}").is_err());
    }
}
