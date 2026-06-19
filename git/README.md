# Useful commands

Use git remote from the repo directory:

```shell
git remote get-url origin
```

That prints the fetch URL for origin (e.g. <https://github.com/julpayne/docs.git>).

Other useful variants:

## All remotes and URLs

```shell
git remote -v
```

## Just origin (fetch + push)

```shell
git remote show origin
```

## Resolve the repo root first, then origin

```shell
git -C /path/to/repo remote get-url origin
```

## If you want the local path of the checkout itself (not the remote):

```shell
git rev-parse --show-toplevel
```
