---
description: "Rust testing patterns — assert_eq!, proptest, integration test modules"
paths:
  - "**/*.rs"
  - "Cargo.toml"
---

# Rust Testing Patterns

Extends `testing-core.md` with Rust-specific guidance. See core for universal anti-patterns and banned assertions.

## Correct Patterns

```rust
// GOOD: Assert exact values with descriptive messages
#[test]
fn test_parse_config() {
    let config = Config::from_str(r#"{"port": 8080}"#).unwrap();
    assert_eq!(config.port, 8080, "port should be parsed from JSON");
    assert_eq!(config.host, "localhost", "host should default to localhost");
}

// GOOD: Test error cases with specific error variants
#[test]
fn test_parse_invalid_json() {
    let err = Config::from_str("{broken").unwrap_err();
    assert!(
        matches!(err, ConfigError::InvalidJson(_)),
        "expected InvalidJson, got {err:?}"
    );
}

// GOOD: Use rstest for parameterized tests
#[rstest]
#[case("ERROR", 25)]
#[case("INFO", 25)]
#[case("DEBUG", 25)]
fn test_filter_by_level(#[case] level: &str, #[case] expected: usize) {
    let results = search(&logs, level);
    assert_eq!(results.len(), expected);
}
```

## Rust Testing Rules

- Use `cargo test` to run all tests
- Use `assert_eq!`/`assert_ne!` over bare `assert!` — they show both values on failure
- Always include a message in `assert!` calls: `assert!(x > 0, "expected positive, got {x}")`
- Put unit tests in `#[cfg(test)] mod tests` inside the source file
- Put integration tests in `tests/` directory (separate compilation)
- Use `rstest` for parameterized tests, `proptest` for property-based
- Use `#[should_panic(expected = "specific message")]` for panic tests
- Use `testcontainers` for integration tests needing real services
