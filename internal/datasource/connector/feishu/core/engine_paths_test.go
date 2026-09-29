package core

import "testing"

// pathNode is the minimal shape folderPathIndex needs; the engine's generic
// NodeOps is exercised end to end by the connector test suites, so these cases
// focus on the walk itself (cycles, dangling parents, blank titles).
type pathNode struct {
	token  string
	title  string
	parent string
}

func buildPathIndex(nodes []pathNode) map[string]string {
	return folderPathIndex(nodes,
		func(n pathNode) string { return n.token },
		func(n pathNode) string { return n.title },
		func(n pathNode) string { return n.parent },
	)
}

func TestFolderPathIndex(t *testing.T) {
	t.Run("flat listing stays at root", func(t *testing.T) {
		idx := buildPathIndex([]pathNode{
			{token: "a", title: "A"},
			{token: "b", title: "B"},
		})
		for _, tok := range []string{"a", "b"} {
			if got := idx[tok]; got != "" {
				t.Errorf("idx[%s] = %q, want root", tok, got)
			}
		}
	})

	t.Run("ancestors become the folder path", func(t *testing.T) {
		idx := buildPathIndex([]pathNode{
			{token: "a", title: "运维"},
			{token: "b", title: "子目录", parent: "a"},
			{token: "c", title: "文档", parent: "b"},
			{token: "sibling", title: "平级"},
		})
		// The node's own title is its file name, never part of its folder.
		if got, want := idx["c"], "运维/子目录"; got != want {
			t.Errorf("idx[c] = %q, want %q", got, want)
		}
		if got, want := idx["b"], "运维"; got != want {
			t.Errorf("idx[b] = %q, want %q", got, want)
		}
		if got := idx["a"]; got != "" {
			t.Errorf("idx[a] = %q, want root", got)
		}
		if got := idx["sibling"]; got != "" {
			t.Errorf("idx[sibling] = %q, want root", got)
		}
	})

	t.Run("dangling parent stops the walk", func(t *testing.T) {
		// The selected resource is not part of the listing, so its direct
		// children point at a token that is absent.
		idx := buildPathIndex([]pathNode{
			{token: "child", title: "文档", parent: "selected-root"},
		})
		if got := idx["child"]; got != "" {
			t.Errorf("idx[child] = %q, want root", got)
		}
	})

	t.Run("parent cycle terminates", func(t *testing.T) {
		// Malformed upstream data must not spin the sync forever.
		idx := buildPathIndex([]pathNode{
			{token: "a", title: "A", parent: "b"},
			{token: "b", title: "B", parent: "a"},
		})
		if _, ok := idx["a"]; !ok {
			t.Fatal("idx[a] missing")
		}
		if _, ok := idx["b"]; !ok {
			t.Fatal("idx[b] missing")
		}
	})

	t.Run("self parent terminates", func(t *testing.T) {
		idx := buildPathIndex([]pathNode{
			{token: "a", title: "A", parent: "a"},
		})
		if got := idx["a"]; got != "" {
			t.Errorf("idx[a] = %q, want root", got)
		}
	})

	t.Run("blank and duplicate tokens", func(t *testing.T) {
		idx := buildPathIndex([]pathNode{
			{token: "", title: "no token"},
			{token: "a", title: "A"},
			{token: "a", title: "A again"},
		})
		if len(idx) != 1 {
			t.Fatalf("len(idx) = %d, want 1: %v", len(idx), idx)
		}
	})

	t.Run("blank ancestor title is dropped from the path", func(t *testing.T) {
		idx := buildPathIndex([]pathNode{
			{token: "a", title: "  "},
			{token: "b", title: "文档", parent: "a"},
		})
		if got := idx["b"]; got != "" {
			t.Errorf("idx[b] = %q, want root (blank ancestor contributes nothing)", got)
		}
	})

	t.Run("ancestor title separator is flattened", func(t *testing.T) {
		idx := buildPathIndex([]pathNode{
			{token: "a", title: "运维/生产"},
			{token: "b", title: "文档", parent: "a"},
		})
		// A folder name carrying "/" must not forge an extra level.
		if got, want := idx["b"], "运维_生产"; got != want {
			t.Errorf("idx[b] = %q, want %q", got, want)
		}
	})
}
