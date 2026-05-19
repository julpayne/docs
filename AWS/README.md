# Creating a ROSA cluster on AWS

## Using AWS

- <https://redhat.atlassian.net/browse/RHOAIENG-3285>
- <https://auth.redhat.com/auth/realms/EmployeeIDP/protocol/saml/clients/itaws>
- <https://github.com/app-sre/rh-aws-saml-login>

This is the console for `us-east-1`:

- <https://us-east-1.console.aws.amazon.com/console/home?region=us-east-1>

## Utilities

- <https://github.com/app-sre/rh-aws-saml-login>

## Using ROSA

Overall instructions:

- <https://docs.google.com/document/d/1DQgZwj0GjCASfolFeAMXQQ1ZFRvFUp_4p1ZI_GDE42c/edit?tab=t.0>

Download the latest version of ROSA from here:

- <https://github.com/openshift/rosa>

Login to ROSA: (see <https://console.redhat.com/openshift/token/rosa>)

```shell
rosa login --use-auth-code
```

In the browser use this account:

```text
Account: iaps-rhods-odh-dev (585132637328)
Region: us-east-1
```

Use account `julpayne+rhaoiaccount` in the browser login page if requested.

Initialize ROSA:

```shell
rosa init --region us-east-1
```
