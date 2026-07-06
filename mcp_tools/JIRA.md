# Using JIRA with Claude

```shell
claude mcp add mcp-atlassian -- uvx mcp-atlassian --jira-url=https://redhat.atlassian.net --jira-username $giveyourid@redhat.com --jira-token $JIRA_API_TOKEN
```

or

```shell
claude mcp add atlassian -t stdio -s user -- npx mcp-remote https://mcp.atlassian.com/v1/mcp
```
