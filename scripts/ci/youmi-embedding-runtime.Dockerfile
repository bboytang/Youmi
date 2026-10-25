ARG PYTHON_RUNTIME=python:3.10-slim
FROM ${PYTHON_RUNTIME}
RUN pip install --no-cache-dir numpy==1.26.4 onnxruntime==1.23.2 tokenizers==0.21.1 openai==2.8.1 httpx==0.28.1 websockets==14.2 tantivy==0.25.1
ENV OMP_NUM_THREADS=2 OPENBLAS_NUM_THREADS=2
WORKDIR /experiment
ENTRYPOINT ["python", "/experiment/scripts/ci/benchmark_youmi_embedding.py"]
