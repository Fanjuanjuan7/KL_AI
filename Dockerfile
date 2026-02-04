# Use official Python runtime as a parent image
FROM python:3.9-slim

# Set working directory
WORKDIR /app

# Install system dependencies
# tk/tcl for Tkinter, xvfb for virtual display if needed
RUN apt-get update && apt-get install -y \
    tk \
    tcl \
    xvfb \
    libgconf-2-4 \
    libnss3 \
    gnupg \
    wget \
    unzip \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Expose health check port
EXPOSE 9999

# Define environment variable
ENV PYTHONPATH=/app
ENV DISPLAY=:99

# Run command (using xvfb to simulate display for Tkinter if no real display)
# This allows the app to start (and the health server to run) even in headless environment
CMD ["sh", "-c", "xvfb-run --server-args='-screen 0 1024x768x24' python -m src.gui_ctk"]
