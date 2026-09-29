package core

import "github.com/Tencent/WeKnora/internal/datasource"

// folder_path.go is fork-only: it holds the source-folder rebuild used by the
// Feishu wiki and Drive connectors, which upstream does not implement. It lives
// in its own file so the customization's footprint in upstream's engine.go
// stays at the NodeOps seam (the ParentToken method and its one call site).

// folderPathIndex rebuilds the source folder hierarchy for one listing, mapping
// each node token to the "/"-joined titles of its ancestors within that
// listing.
//
// List returns a flat slice, but every node carries its parent token, so the
// tree is reconstructible with no further API calls. Only ancestors contribute:
// a node's own title is a file name, not part of its folder path.
//
// The caller-selected resource does not appear in nodes, so a direct child of
// it walks to an absent parent and lands at "" - the knowledge base root. That
// is the intended "paths start at the first folder inside the selected scope"
// layout.
func folderPathIndex[N any](
	nodes []N,
	token func(N) string, title func(N) string, parentToken func(N) string,
) map[string]string {
	titles := make(map[string]string, len(nodes))
	parents := make(map[string]string, len(nodes))
	for _, node := range nodes {
		tok := token(node)
		if tok == "" {
			continue
		}
		titles[tok] = title(node)
		parents[tok] = parentToken(node)
	}

	idx := make(map[string]string, len(titles))
	ancestors := make([]string, 0, 8)
	for tok := range titles {
		ancestors = ancestors[:0]
		// seen terminates a parent cycle in malformed upstream data: the walk
		// must never spin.
		seen := map[string]bool{tok: true}
		for cur := parents[tok]; cur != "" && !seen[cur]; cur = parents[cur] {
			seen[cur] = true
			title, ok := titles[cur]
			if !ok {
				break // parent lies outside this listing
			}
			ancestors = append(ancestors, title)
		}
		// Collected nearest-first; reverse to root-first.
		for i, j := 0, len(ancestors)-1; i < j; i, j = i+1, j-1 {
			ancestors[i], ancestors[j] = ancestors[j], ancestors[i]
		}
		idx[tok] = datasource.JoinKnowledgeFolderPath(ancestors)
	}
	return idx
}
