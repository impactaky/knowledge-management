"""Small CLI for standalone Catalog, candidate search and explicit grep."""
import argparse
import json
from .core import get_catalog, grep, search


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--catalog')
    sub = parser.add_subparsers(dest='operation', required=True)
    sub.add_parser('catalog')
    candidate = sub.add_parser('search')
    candidate.add_argument('query')
    candidate.add_argument('--shallow', action='store_true')
    literal = sub.add_parser('grep')
    literal.add_argument('query')
    args = parser.parse_args()
    if args.operation == 'catalog':
        print(get_catalog(args.catalog), end='')
    else:
        result = (search(args.query, deep=not args.shallow, catalog_path=args.catalog)
                  if args.operation == 'search' else grep(args.query, catalog_path=args.catalog))
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
