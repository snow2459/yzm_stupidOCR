## 验证码识别
油猴脚本为
[my.js](./my.js)
端口6688
## 运行
python 3.9
```shell
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
```
## docker打包
x64
```shell
docker buildx build --platform linux/amd64 -t stupidocr:x64 .
```

arm
```shell
docker buildx build --platform linux/arm64 -t stupidocr:arm64 .
```