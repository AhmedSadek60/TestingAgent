package com.acme;

import dev.langchain4j.agent.tool.Tool;

public class Tools {
    @Tool("Search the product catalog by keyword")
    public String searchCatalog(String keyword) { return "..."; }

    @Tool("Delete a customer account permanently")
    public String deleteAccount(String accountId) { return "deleted"; }
}
