#!/bin/bash

for f in *.pdf
do
  pdftk $f background /home/zhou/Dropbox/wechat/logo/HiMCM/watermark1.pdf output temp.pdf
  
  rm $f
  mv temp.pdf $f

  pdftk $f stamp /home/zhou/Dropbox/wechat/logo/HiMCM/watermark2.pdf output temp.pdf

  rm $f
  mv temp.pdf $f
done

