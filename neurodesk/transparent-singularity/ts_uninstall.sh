#!/bin/bash

while IFS= read -r executable; do
      echo "$executable"
      rm "$executable"
done < commands.txt

rm -rf activate*
rm -rf deactivate*
rm commands.txt
rm -- *.sif

