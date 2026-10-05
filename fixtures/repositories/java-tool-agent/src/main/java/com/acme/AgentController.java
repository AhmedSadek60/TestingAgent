package com.acme;

import org.springframework.web.bind.annotation.*;

@RestController
public class AgentController {
    @PostMapping("/ask")
    public String ask(@RequestBody String q) { return "..."; }
}
