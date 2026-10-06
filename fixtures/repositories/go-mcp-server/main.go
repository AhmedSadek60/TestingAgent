package main

import (
    "github.com/mark3labs/mcp-go/mcp"
    "github.com/mark3labs/mcp-go/server"
)

func main() {
    s := server.NewMCPServer("notes", "1.0.0")
    s.AddTool(mcp.NewTool("add_note", mcp.WithDescription("Create a new note")), addNote)
    s.AddTool(mcp.NewTool("list_notes", mcp.WithDescription("List all notes")), listNotes)
    server.ServeStdio(s)
}
