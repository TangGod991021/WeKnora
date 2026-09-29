package datasource

import (
	"strings"
	"unicode/utf8"

	"github.com/Tencent/WeKnora/internal/types"
)

// JoinKnowledgeFolderPath turns ordered ancestor names into a knowledge-base
// folder path, or "" when nothing survives sanitizing.
//
// Segments are sanitized individually and then joined with "/". Sanitizing the
// already-joined string would destroy the hierarchy: SanitizeFileName replaces
// "/" with "_", so "运维/子目录" would collapse into a single flat name. Every
// caller that builds a path from external names must go through here for the
// same reason.
//
// The result is capped at the same depth/segment/total limits the folder_path
// column enforces (types.MaxKnowledgeFolder*). Over-deep source trees are
// trimmed from the deepest end here rather than being silently truncated by
// NormalizeKnowledgeFolderPath downstream, so a connector can tell what it is
// actually about to store.
func JoinKnowledgeFolderPath(segments []string) string {
	clean := make([]string, 0, len(segments))
	for _, segment := range segments {
		segment = strings.TrimSpace(segment)
		if segment == "" {
			continue
		}
		segment = truncateBytes(SanitizeFileName(segment), types.MaxKnowledgeFolderSegmentLength)
		if segment == "" {
			continue
		}
		clean = append(clean, segment)
	}

	// Keep the shallowest levels: a reader locates a document by the top of the
	// tree, so the tail is what can be dropped without losing meaning. This
	// mirrors how NormalizeKnowledgeFolderPath degrades an over-long path.
	if len(clean) > types.MaxKnowledgeFolderDepth {
		clean = clean[:types.MaxKnowledgeFolderDepth]
	}
	for len(clean) > 0 {
		joined := strings.Join(clean, "/")
		if len(joined) <= types.MaxKnowledgeFolderPathLength {
			return joined
		}
		clean = clean[:len(clean)-1]
	}
	return ""
}

// WithKnowledgeFolderPath files baseName under folderPath, returning baseName
// unchanged when there is no folder to add.
func WithKnowledgeFolderPath(folderPath, baseName string) string {
	if folderPath == "" || baseName == "" {
		return baseName
	}
	return folderPath + "/" + baseName
}

// truncateBytes cuts s to at most maxBytes without splitting a UTF-8 rune.
func truncateBytes(s string, maxBytes int) string {
	if len(s) <= maxBytes {
		return s
	}
	s = s[:maxBytes]
	for len(s) > 0 {
		r, size := utf8.DecodeLastRuneInString(s)
		if r != utf8.RuneError || size != 1 {
			break
		}
		s = s[:len(s)-1]
	}
	return s
}
