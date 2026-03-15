# Use a modern, slim Python image for performance and efficiency
FROM python:3.13-slim

# Set working directory
WORKDIR /app

# Install system dependencies (for building some python packages if needed)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application
COPY . .

# Create directories for persistence
RUN mkdir -p /data/backups /data/outputs

# Environment variables
ENV PORT=8080
ENV DATABASE_URL=sqlite:////data/data.db
ENV PYTHONUNBUFFERED=1

# Expose the dashboard port
EXPOSE 8080

# The app uses /data for all persistent state
VOLUME /data

# Start the application
CMD ["python", "app.py"]
