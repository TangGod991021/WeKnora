package datasource

import (
	"strings"
	"testing"
	"unicode/utf8"

	"github.com/Tencent/WeKnora/internal/types"
)

func TestJoinKnowledgeFolderPath(t *testing.T) {
	cases := []struct {
		name, want string
		input      []string
	}{
		{"empty", "", nil},
		{"single", "运维", []string{"运维"}},
		{"nested", "运维/子目录", []string{"运维", "子目录"}},
		{"blank segments dropped", "运维/子目录", []string{"运维", "", "   ", "子目录"}},
		{"surrounding space trimmed", "运维", []string{"  运维  "}},
		// The whole point of sanitizing per segment: a name carrying a path
		// separator must not be able to forge hierarchy, and the separator
		// between real segments must survive.
		{"separator inside a segment flattened", "a_b/c", []string{"a/b", "c"}},
		{"backslash inside a segment flattened", "a_b", []string{`a\b`}},
		{"colon inside a segment flattened", "a_b", []string{"a:b"}},
	}
	for _, tt := range cases {
		t.Run(tt.name, func(t *testing.T) {
			got := JoinKnowledgeFolderPath(tt.input)
			if got != tt.want {
				t.Fatalf("JoinKnowledgeFolderPath(%q) = %q, want %q", tt.input, got, tt.want)
			}
		})
	}
}

func TestJoinKnowledgeFolderPathEnforcesLimits(t *testing.T) {
	t.Run("depth", func(t *testing.T) {
		segments := make([]string, types.MaxKnowledgeFolderDepth+5)
		for i := range segments {
			segments[i] = "d"
		}
		got := JoinKnowledgeFolderPath(segments)
		if got == "" {
			t.Fatal("expected a non-empty path")
		}
		if n := strings.Count(got, "/") + 1; n != types.MaxKnowledgeFolderDepth {
			t.Fatalf("depth = %d, want %d (path %q)", n, types.MaxKnowledgeFolderDepth, got)
		}
	})

	t.Run("segment length", func(t *testing.T) {
		got := JoinKnowledgeFolderPath([]string{strings.Repeat("a", 500)})
		if len(got) > types.MaxKnowledgeFolderSegmentLength {
			t.Fatalf("segment length = %d, want <= %d", len(got), types.MaxKnowledgeFolderSegmentLength)
		}
	})

	t.Run("total length", func(t *testing.T) {
		segments := make([]string, types.MaxKnowledgeFolderDepth)
		for i := range segments {
			segments[i] = strings.Repeat("a", types.MaxKnowledgeFolderSegmentLength)
		}
		got := JoinKnowledgeFolderPath(segments)
		if len(got) > types.MaxKnowledgeFolderPathLength {
			t.Fatalf("path length = %d, want <= %d", len(got), types.MaxKnowledgeFolderPathLength)
		}
		// Trimming drops the deepest levels, so the shallowest must survive.
		if !strings.HasPrefix(got, "a") {
			t.Fatalf("shallowest segment was dropped: %q", got)
		}
	})

	t.Run("cjk stays valid utf8", func(t *testing.T) {
		segments := make([]string, 20)
		for i := range segments {
			segments[i] = strings.Repeat("测", 50)
		}
		got := JoinKnowledgeFolderPath(segments)
		if !utf8.ValidString(got) {
			t.Fatalf("split a UTF-8 rune: %q", got)
		}
	})
}

func TestWithKnowledgeFolderPath(t *testing.T) {
	cases := []struct{ name, folder, base, want string }{
		{"no folder", "", "doc.md", "doc.md"},
		{"folder", "运维/子目录", "doc.md", "运维/子目录/doc.md"},
		{"no base name", "运维", "", ""},
		{"neither", "", "", ""},
	}
	for _, tt := range cases {
		t.Run(tt.name, func(t *testing.T) {
			got := WithKnowledgeFolderPath(tt.folder, tt.base)
			if got != tt.want {
				t.Fatalf("WithKnowledgeFolderPath(%q, %q) = %q, want %q", tt.folder, tt.base, got, tt.want)
			}
		})
	}
}
