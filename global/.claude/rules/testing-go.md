---
description: "Go testing patterns — table-driven tests, testify, test isolation"
paths:
  - "**/*.go"
  - "go.mod"
---

# Go Testing Patterns

Extends `testing-core.md` with Go-specific guidance. See core for universal anti-patterns and banned assertions.

## Correct Patterns

```go
// GOOD: Table-driven tests with exact expected values
func TestParseConfig(t *testing.T) {
    tests := []struct {
        name     string
        input    string
        expected Config
        wantErr  string
    }{
        {
            name:     "valid config",
            input:    `{"port": 8080, "host": "localhost"}`,
            expected: Config{Port: 8080, Host: "localhost"},
        },
        {
            name:    "invalid JSON",
            input:   `{broken`,
            wantErr: "invalid character",
        },
    }
    for _, tt := range tests {
        t.Run(tt.name, func(t *testing.T) {
            got, err := ParseConfig(tt.input)
            if tt.wantErr != "" {
                require.ErrorContains(t, err, tt.wantErr)
                return
            }
            require.NoError(t, err)
            assert.Equal(t, tt.expected, got)
        })
    }
}
```

## Go Testing Rules

- Use `go test ./...` to run all tests
- Use `testify/require` for fatal checks, `testify/assert` for non-fatal
- Table-driven tests for multiple inputs — name every case
- Use `t.Parallel()` for independent tests
- Use `t.Helper()` in test utility functions
- Use `t.Cleanup()` instead of `defer` for test resource cleanup
- Test packages with `_test` suffix for black-box testing of public API
- Use `testcontainers-go` for integration tests needing real services
