pipeline {
    agent any

    options {
        timestamps()
        disableConcurrentBuilds()
    }

    parameters {
        string(name: 'DOCKERHUB_NAMESPACE', defaultValue: 'your-dockerhub-username', description: 'Docker Hub username or organization')
        string(name: 'IMAGE_NAME', defaultValue: 'egypt-used-cars-airflow', description: 'Docker Hub image repository name')
    }

    environment {
        DOCKERHUB_CREDENTIALS_ID = 'dockerhub-creds'
        IMAGE_TAG = "${params.DOCKERHUB_NAMESPACE}/${params.IMAGE_NAME}:${env.BUILD_NUMBER}"
        LATEST_TAG = "${params.DOCKERHUB_NAMESPACE}/${params.IMAGE_NAME}:latest"
    }

    stages {
        stage('Validate Compose') {
            steps {
                sh 'docker compose config --quiet'
            }
        }

        stage('Build Image') {
            steps {
                sh 'docker build -f Dockerfile.airflow -t "$IMAGE_TAG" -t "$LATEST_TAG" .'
            }
        }

        stage('Validate Python') {
            steps {
                sh 'docker run --rm "$IMAGE_TAG" bash -c "python -m py_compile /opt/airflow/src/*.py /opt/airflow/dags/*.py"'
            }
        }

        stage('Push To Docker Hub') {
            when {
                anyOf {
                    branch 'main'
                    branch 'master'
                    expression { return env.BRANCH_NAME == null }
                }
            }
            steps {
                withCredentials([usernamePassword(credentialsId: env.DOCKERHUB_CREDENTIALS_ID, usernameVariable: 'DOCKERHUB_USER', passwordVariable: 'DOCKERHUB_TOKEN')]) {
                    sh '''
                        echo "$DOCKERHUB_TOKEN" | docker login -u "$DOCKERHUB_USER" --password-stdin
                        docker push "$IMAGE_TAG"
                        docker push "$LATEST_TAG"
                        docker logout
                    '''
                }
            }
        }
    }

    post {
        always {
            sh 'docker image prune -f || true'
        }
    }
}
