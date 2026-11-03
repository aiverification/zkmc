FROM mambaorg/micromamba:1.5.10
RUN micromamba install -y -n base -c conda-forge spot && micromamba clean -a -y
ENV PATH=/opt/conda/bin:$PATH
