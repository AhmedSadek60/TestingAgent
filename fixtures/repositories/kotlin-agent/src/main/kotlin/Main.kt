import dev.langchain4j.agent.tool.Tool

class Tools {
    @Tool("Convert currency amounts")
    fun convert(amount: Double, from: String, to: String): String = "0"
}
