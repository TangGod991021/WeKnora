package service

import (
	"testing"

	"github.com/Tencent/WeKnora/internal/types"
)

// TestUsesSourceIdentityDuplicateCheck pins which channels deduplicate by
// source path instead of by content. A connector must only be listed here once
// it actually emits path-qualified FileNames: listing it early turns a
// legitimate duplicate upload into two separate knowledge items.
func TestUsesSourceIdentityDuplicateCheck(t *testing.T) {
	bySourcePath := []string{
		types.ConnectorTypeGitLab,
		types.ChannelConfluence,
		types.ChannelFeishu,
		types.ChannelFeishuDrive,
		types.ChannelLarkDrive,
		types.ChannelYuque,
	}
	for _, channel := range bySourcePath {
		if !usesSourceIdentityDuplicateCheck(channel) {
			t.Errorf("channel %q must deduplicate by source path", channel)
		}
	}

	byContent := []string{
		"",
		types.ChannelNotion,
		types.ChannelRSS,
		types.ChannelIMA,
		types.ChannelDingtalk,
		"something-unknown",
	}
	for _, channel := range byContent {
		if usesSourceIdentityDuplicateCheck(channel) {
			t.Errorf("channel %q must keep content deduplication", channel)
		}
	}
}
